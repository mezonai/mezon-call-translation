"""
Service for generating room summaries
"""

from datetime import datetime
from typing import Optional, Dict, Any
from bson import ObjectId

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.services.llm.factory import create_llm_service
from orchestrator_service.config.application_config import get_config

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class SummaryService:
    """Service to handle room summarization logic"""

    def __init__(self):
        self.pg_repo = PgTranscriptRepository()
        self.config = get_config()
        # Create LLM service based on configured provider
        self.llm_service = create_llm_service(self.config.llm)
        logger.info(
            f"SummaryService initialized with LLM provider: {self.config.llm.provider}"
        )

    async def generate_summary(self, room_id: ObjectId) -> Optional[Dict[str, Any]]:
        """
        Generate a summary for the given room_id.

        Currently implements a concatenation strategy:
        1. Verify room exists.
        2. Get all tracks.
        3. Collect all segments.
        4. Sort by absolute time.
        5. Collect Full Text and Participants.
        6. Save transcript draft to DB.
        7. Generate Summary via LLM.
        8. Update summary in DB.
        9. Notify clients via SSE.
        """
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

        # 1. Verify room exists
        room_doc = await self.pg_repo.get_room_by_id(room_id)
        if not room_doc:
            logger.error(f"Generate Summary: Room not found for id: {room_id}")
            return None

        # 2. Get All Tracks
        tracks = await self.pg_repo.get_tracks_by_room(
            room_id, status="completed"
        )
        if not tracks:
            logger.warning(f"No tracks found for room {room_id}")
            return None

        # 3. Collect all segments with absolute timestamps in one pass
        all_segments = []

        for track in tracks:
            try:
                participant = track.get("participant_identity", "Unknown")
                track_start_ns = int(
                    track.get("audio_info", {}).get("started_at_ns", 0) or 0
                )
                chunks = await self.pg_repo.get_chunks_by_track(
                    str(track["_id"]), sorted_by_index=True
                )

                for chunk in chunks:
                    for seg in chunk.get("segments", []):
                        text = seg.get("text", "").strip()
                        if not text:
                            continue

                        seg_start_ns = track_start_ns + int(
                            (seg.get("start") or 0.0) * 1_000_000_000
                        )

                        all_segments.append(
                            {
                                "timestamp": seg_start_ns,
                                "participant_id": participant,
                                "text": text,
                            }
                        )
            except Exception as e:
                logger.error(f"Error processing track {track.get('_id')}: {e}")
                continue

        if not all_segments:
            logger.warning(f"No transcript segments found for room {room_id}")
            return None

        all_segments.sort(key=lambda x: x["timestamp"])

        # 4. Collect Full Text and Participants
        unique_participants = {seg["participant_id"] for seg in all_segments}

        turns = []
        current_turn = None

        for seg in all_segments:
            participant = seg["participant_id"]
            text = seg["text"]

            if current_turn and current_turn["participant_id"] == participant:
                current_turn["content"] += f"\n{text}"
            else:
                if current_turn:
                    turns.append(current_turn)
                dt = datetime.fromtimestamp(seg["timestamp"] / 1_000_000_000)
                current_turn = {
                    "timestamp": dt.strftime("%H:%M:%S"),
                    "participant_id": participant,
                    "content": text,
                }

        if current_turn:
            turns.append(current_turn)

        # full_text is used for LLM summarization and also saved in DB to allow retrying LLM if summary generation fails. It can be a long string, but we keep it as is for now since it's needed for the summary generation step. In the future, we could consider storing it in a more efficient way if we find performance issues with very long conversations.
        full_text = "\n".join(
            f"[{t['timestamp']}] {t['participant_id']}: {t['content']}" for t in turns
        )

        draft_summary: Dict[str, Any] = {
            "room_id": room_id,
            "room_name": room_doc.get("room_name", "Unknown"),
            "participants": list(unique_participants),
            "summary_data": {},
            "full_text": full_text,
            "messages": turns,
            "created_at": datetime.utcnow(),
            "total_segments": len(all_segments),
        }

        # 5. Save Summary to PostgreSQL
        try:
            saved_id = await self.pg_repo.save_room_summary(draft_summary)
            if not saved_id:
                logger.error(f"Failed to save summary for room: {room_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to save summary to DB: {e}")
            return None

        try:
            # 7. Generate Summary via LLM (uses configured provider)
            summary_data_result = await self.llm_service.summarize_conversation(
                conversation_text=full_text, language=self.config.llm.language
            )
            action_items = summary_data_result.action_items
            action_items_dict = {
                action_item.participant_identity: action_item.participant_actions
                for action_item in action_items
            }
            summary_data = {
                "summary": summary_data_result.summary,
                "action_items": action_items_dict,
            }

            final_summary = dict(draft_summary)
            final_summary["summary_data"] = summary_data

            # 8. Update only summary_data in DB; full_text remains from the draft save
            updated = await self.pg_repo.update_room_summary(
                room_id, summary_data
            )
            if not updated:
                logger.error(f"Failed to update generated summary for room {room_id}")
                return {**draft_summary, "_id": saved_id}

            logger.info(f"Generated summary for room {room_id} (ID: {saved_id})")
            result = dict(final_summary)
            result["_id"] = saved_id

            # 9. Notify clients via SSE if summary generation is successful
            metadata_channel = MetadataChannel()
            await metadata_channel.push_room_summary_done(
                room_id=str(room_id), room_name=room_doc.get("room_name", "Unknown")
            )
            return result
        except Exception as e:
            logger.error(f"Failed to generate summary for room {room_id}: {e}")
            return {**draft_summary, "_id": saved_id}

    async def retry_summary_from_full_text(
        self, room_id: ObjectId
    ) -> Optional[Dict[str, Any]]:
        """
        Hotfix: re-run LLM summarization using the full_text already stored in rooms_summary.
        Used when LLM service fails in the first run and summary_data is missing.

        Returns:
            summary_data dict if successful, None if failed.

        Raises:
            ValueError: If document not found or full_text is empty.
        """
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

        summary_doc = await self.pg_repo.get_summary_by_room_id(room_id)
        if not summary_doc:
            raise ValueError(f"Not found summary_doc for room_id: {room_id}")

        full_text: str = summary_doc.get("full_text", "").strip()
        if not full_text:
            raise ValueError(f"full_text is empty for room_id: {room_id}")

        logger.info(f"Retrying LLM for room {room_id} ({len(full_text)} chars)")

        try:
            result = await self.llm_service.summarize_conversation(
                conversation_text=full_text,
                language=self.config.llm.language,
            )

            summary_data = {
                "summary": result.summary,
                "action_items": {
                    item.participant_identity: item.participant_actions
                    for item in result.action_items
                },
            }

            updated = await self.pg_repo.update_room_summary(room_id, summary_data)
            logger.info(f"Updated summary for room {room_id}")
            if not updated:
                logger.error(f"Failed to update summary for room {room_id}")
                return None

            # Notify clients via SSE if summary generation is successful
            metadata_channel = MetadataChannel()
            await metadata_channel.push_room_summary_done(
                room_id=room_id, room_name=summary_doc.get("room_name", "Unknown")
            )

            logger.info(
                f"Successfully updated summary for room {room_id} and notified clients"
            )
            return summary_data
        except Exception as e:
            logger.error(f"Failed to retry summary for room {room_id}: {e}")
            return None


# Singleton
_summary_service = None


def get_summary_service() -> SummaryService:
    global _summary_service
    if _summary_service is None:
        _summary_service = SummaryService()
    return _summary_service
