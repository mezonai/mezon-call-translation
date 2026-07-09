"""
Service for generating room summaries
"""

import asyncio
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from pydantic import BaseModel
from fastapi import HTTPException

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.services.postgresql.pg_outbox_repository import PgOutboxRepository, get_pg_outbox_repository
from orchestrator_service.services.postgresql.pg_summary_repository import PgSummaryRepository, get_pg_summary_repository
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository, get_pg_transcript_repository
from orchestrator_service.services.llm.llm_factory import create_llm_service
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_prompt_summary, build_prompt_action_items
from orchestrator_service.models.summary_models import SummaryResult, ActionItemsResult
from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.summary_models import RetryType, RoomSummaryResponse
from orchestrator_service.services.llm.factory import create_llm_service
from orchestrator_service.services.postgresql.models import TranscriptChunk
from orchestrator_service.services.postgresql.pg_outbox_repository import PgOutboxRepository, get_pg_outbox_repository
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
    get_pg_transcript_repository,
)
from orchestrator_service.utils.time_convert import convert_to_iso_8601
from orchestrator_service.services.light_summary_service import LightSummaryService
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)


class SummaryService:
    """Service to handle room summarization logic"""

    def __init__(self,
                pg_transcript_repo: PgTranscriptRepository,
                pg_summary_repo: PgSummaryRepository,
                outbox_repo: PgOutboxRepository,
                light_summary_service: Any,
                llm_service: BaseLLMService,
                llm_service_fallback: Optional[BaseLLMService] = None,
                ):
        self.pg_transcript_repo = pg_transcript_repo
        self.pg_summary_repo = pg_summary_repo
        self.outbox_repo = outbox_repo
        self.config = get_config().summary
        self.light_summary_service = light_summary_service
        self.llm_service = llm_service
        self.llm_service_fallback = llm_service_fallback
        logger.info(
            f"SummaryService initialized with LLM provider: {self.config.provider}"
        )

    async def _call_llm_with_fallback(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        for attempt in range(1, self.config.retry_count + 1):
            try:
                return await self.llm_service.generate(
                    prompt=prompt,
                    response_model=response_model,
                    model=self.config.model,
                    timeout=self.config.timeout
                )
            except Exception as e:
                logger.error(f"[Primary LLM] attempt {attempt}/{self.config.retry_count} failed: {e}")
                if attempt == self.config.retry_count:
                    if self.config.fallback_enable and self.llm_service_fallback:
                        logger.warning("All primary attempts failed. Switching to fallback LLM.")
                        for fb_attempt in range(1, self.config.fallback_retry_count + 1):
                            try:
                                return await self.llm_service_fallback.generate(
                                    prompt=prompt,
                                    response_model=response_model,
                                    model=self.config.fallback_model,
                                    timeout=self.config.fallback_timeout
                                )
                            except Exception as fb_e:
                                logger.error(f"[Fallback LLM] attempt {fb_attempt}/{self.config.fallback_retry_count} failed: {fb_e}")
                                if fb_attempt == self.config.fallback_retry_count:
                                    raise fb_e
                                await asyncio.sleep(2)
                    else:
                        raise e
                await asyncio.sleep(2)




    async def generate_summary(self, room_id: str) -> Optional[Dict[str, Any]]:
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
        if not self.pg_transcript_repo.connected:
            await self.pg_transcript_repo.connect()

        # 1. Verify room exists
        room_doc = await self.pg_transcript_repo.get_room_by_id(room_id)
        if not room_doc:
            logger.error(f"Generate Summary: Room not found for id: {room_id}")
            return None

        # 2. Get All Tracks
        tracks = await self.pg_transcript_repo.get_tracks_by_room(
            room_id, status="completed"
        )
        if not tracks:
            logger.warning(f"No tracks found for room {room_id}")
            return None

        # 3. Collect all segments with absolute timestamps in one pass
        # TODO: Use `Any` type because the `type` data to insert into `all_segments` has unknown type
        all_segments: list[dict[str, Any]] = []  # type: ignore[explicit-any]
        participant_durations: dict[str, float] = {}

        track_ids = [str(track.id) for track in tracks]
        all_chunks = await self.pg_transcript_repo.get_chunks_by_track_ids(track_ids, sorted_by_index=True)
        
        chunks_by_track = {tid: [] for tid in track_ids}
        for chunk in all_chunks:
            tid = str(chunk.track_ref_id)
            if tid in chunks_by_track:
                chunks_by_track[tid].append(chunk)

        for track in tracks:
            try:
                participant = track.participant_identity or "Unknown"
                audio_info = track.audio_info or {}
                track_start_ns = int(audio_info.get("started_at_ns", 0) or 0)
                chunks = chunks_by_track[track.id]

                # Calculate duration for this track/participant using start_time and end_time of chunks
                track_duration = sum((c.end_time or 0.0) - (c.start_time or 0.0) for c in chunks)
                if participant != "Unknown":
                    participant_durations[participant] = participant_durations.get(participant, 0.0) + track_duration

                for chunk in chunks:
                    for seg in chunk.segments or []:
                        text = seg.get("text", "").strip()
                        if not text:
                            continue

                        seg_start_ns = track_start_ns + int((seg.get("start") or 0.0) * 1_000_000_000)

                        all_segments.append(
                            {
                                "timestamp": seg_start_ns,
                                "participant_id": participant,
                                "text": text,
                            }
                        )
            except Exception as e:
                logger.error(f"Error processing track {track.id}: {e}")
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

        # Update room participants list in-place with their calculated speech durations and save
        room_participants_raw = room_doc.participants

        # TODO: Use `Any` type because the `room_participant_raw` data to insert into `room_participants` has complex type
        room_participants: list[dict[str, Any]] = (  # type: ignore[explicit-any]
            room_participants_raw if isinstance(room_participants_raw, list) else []
        )

        for p in room_participants:
            user_id = p.get("participant_identity")
            if user_id:
                p["duration"] = round(participant_durations.get(user_id, 0.0), 2)

        try:
            await self.pg_transcript_repo.update_room_participants(room_id, room_participants)
        except Exception as e:
            logger.error(f"Failed to save speech durations to database: {e}")

        # full_text is used for LLM summarization.
        # It can be a long string, but we keep it as is for now since it's needed for the summary generation step.
        # In the future, we could consider storing it in a more efficient way if we find performance issues with very long conversations.
        full_text = "\n".join(f"[{t['timestamp']}] {t['participant_id']}: {t['content']}" for t in turns)

        draft_summary: dict[str, Any] = {  # type: ignore[explicit-any]
            "room_id": room_id,
            "room_name": room_doc.room_name or "Unknown",
            "participants": list(unique_participants),
            "summary_data": {},
            "messages": turns,
            "created_at": datetime.utcnow(),
            "total_segments": len(all_segments),
        }

        # 5. Save Summary to PostgreSQL
        try:
            saved_id = await self.pg_summary_repo.save_room_summary(draft_summary)
            if not saved_id:
                logger.error(f"Failed to save summary for room: {room_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to save summary to DB: {e}")
            return None

        total_duration = 0.0
        if room_doc.created_at and room_doc.finalized_at:
            total_duration = (room_doc.finalized_at - room_doc.created_at).total_seconds()

        if total_duration > self.config.threshold_min * 60:
            logger.info(f"Room duration ({total_duration:.1f}s) > {self.config.threshold_min} mins. Using Light Summary flow.")
            
            try:
                # Light summary flow
                summary_data = await self.light_summary_service.process_room_by_id(room_id, language=self.config.language)
                final_summary = dict(draft_summary)
                final_summary["summary_data"] = summary_data

                logger.info(f"Generated light summary for room {room_id} (ID: {saved_id})")

                result = dict(final_summary)
                result["id"] = saved_id
                
                metadata_channel = MetadataChannel()
                await metadata_channel.push_room_summary_done(room_id=str(room_id), room_name=(room_doc.room_name or "Unknown"))

                return result
            
            except Exception as e:
                logger.error(f"Failed to generate light summary for room {room_id}: {e}")
                logger.warning(f"Light summary task failed for room {room_id}. Creating outbox task.")

                await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id),
                    retry_type=RetryType.ALL,
                    error_msg=str(e)
                )
                return {**draft_summary, "id": saved_id}
        else:
            # Normal summary flow
            logger.info(f"Room duration ({total_duration:.1f}s) <= {self.config.threshold_min} mins. Using Normal Summary flow.")
            try:
                # 7. Generate Summary via LLM (uses configured provider)
                summary_prompt = build_prompt_summary(full_text, self.config.language)
                summary_data_result = await self._call_llm_with_fallback(summary_prompt, SummaryResult)
                action_prompt = build_prompt_action_items(full_text, self.config.language)
                action_result = await self._call_llm_with_fallback(action_prompt, ActionItemsResult)
                
                summary_parts = [f"Context\n{summary_data_result.context}"]
                if summary_data_result.key_discussions:
                    summary_parts.append("Key Discussions\n" + "\n".join(summary_data_result.key_discussions))
                if summary_data_result.next_focus:
                    summary_parts.append("Next Focus\n" + "\n".join(summary_data_result.next_focus))
                if summary_data_result.detail:
                    summary_parts.append("Detail\n" + "\n".join(summary_data_result.detail))
                summary_data = {
                    "summary": "\n\n".join(summary_parts),
                    "action_items": {
                        item.participant_identity: item.participant_actions
                        for item in action_result.action_items
                    },
                }

                final_summary = dict(draft_summary)
                final_summary["summary_data"] = summary_data

                # 8. Update only summary_data in DB; full_text remains from the draft save
                updated = await self.pg_summary_repo.update_room_summary(
                    room_id, summary_data
                )
                if not updated:
                    logger.error(f"Failed to update generated summary for room {room_id}")
                    return {**draft_summary, "id": saved_id}

                logger.info(f"Generated summary for room {room_id} (ID: {saved_id})")
                result = dict(final_summary)
                result["id"] = saved_id

                # 9. Notify clients via SSE if summary generation is successful
                metadata_channel = MetadataChannel()
                await metadata_channel.push_room_summary_done(
                    room_id=str(room_id), room_name=room_doc.room_name or "Unknown"
                )
            
            except Exception as e:
                logger.error(f"Failed to generate summary for room {room_id}: {e}")
                logger.warning(f"ALL summarization task failed for room {room_id}. Creating outbox task.")
                
                await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id),
                    retry_type=RetryType.ALL,
                    error_msg=str(e)
                )
                return {**draft_summary, "id": saved_id}

    # TODO: Use `Any` type because `summary_data` response from this function has complex type
    async def retry_summary_from_full_text(  # type: ignore[explicit-any]
        self, room_id: str, retry_type: RetryType = RetryType.ALL
    ) -> dict[str, Any] | None:
        """
        Hotfix: re-run LLM summarization by rebuilding full_text from messages stored in rooms_summary.
        Used when LLM service fails in the first run and summary_data is missing.

        Returns:
            summary_data dict if successful, None if failed.

        Raises:
            ValueError: If document not found or messages is empty.
        """
        if not self.pg_summary_repo.connected:
            await self.pg_summary_repo.connect()

        summary_doc, room_doc = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        if not summary_doc:
            raise ValueError(f"Not found summary_doc for room_id: {room_id}")

        messages = summary_doc.messages or []
        if not messages:
            raise ValueError(f"messages is empty for room_id: {room_id}")

        full_text = "\n".join(f"[{t['timestamp']}] {t['participant_id']}: {t['content']}" for t in messages)

        total_duration = 0.0
        if room_doc.created_at and room_doc.finalized_at:
            total_duration = (room_doc.finalized_at - room_doc.created_at).total_seconds()

        if total_duration > self.config.threshold_min * 60:
            logger.info(f"Retrying room ({total_duration:.1f}s) > {self.config.threshold_min} mins. Using Light Summary flow.")

            try:
                summary_data = await self.light_summary_service.process_room_by_id(room_id, language=self.config.language)

                metadata_channel = MetadataChannel()
                await metadata_channel.push_room_summary_done(room_id=room_id, room_name=summary_doc.room_name or "Unknown")

                return summary_data
            except Exception as e:
                logger.error(f"Failed to retry light summary for room {room_id}: {e}")
                return None
        else:
            logger.info(f"Retrying room ({total_duration:.1f}s) <= {self.config.threshold_min} mins. Using Normal Summary flow.")
            
            # Extract existing summary_data to preserve fields that aren't being retried
            existing_summary_data = summary_doc.summary_data or {}
            existing_summary = existing_summary_data.get("summary", "")
            existing_action_items = existing_summary_data.get("action_items", {})

            logger.info(f"Retrying LLM with type '{retry_type.value}' for room {room_id} ({len(full_text)} chars)")

            is_success = False
            try:
                if retry_type == RetryType.SUMMARY:
                    prompt = build_prompt_summary(full_text, self.config.language)
                    result = await self._call_llm_with_fallback(prompt, SummaryResult)

                    # Format summary with only non-empty fields
                    summary_parts = [f"Context\n{result.context}"]

                    if result.key_discussions:
                        summary_parts.append("Key Discussions\n" + "\n".join(result.key_discussions))

                    if result.next_focus:
                        summary_parts.append("Next Focus\n" + "\n".join(result.next_focus))

                    if result.detail:
                        summary_parts.append("Detail\n" + "\n".join(result.detail))

                    summary_data = {
                        "summary": "\n\n".join(summary_parts),
                        "action_items": existing_action_items,
                    }

                    is_success = True

                elif retry_type == RetryType.ACTION_ITEMS:
                    prompt = build_prompt_action_items(full_text, self.config.language)
                    result = await self._call_llm_with_fallback(prompt, ActionItemsResult)

                    summary_data = {
                        "summary": existing_summary,
                        "action_items": {
                            item.participant_identity: item.participant_actions
                            for item in result.action_items
                        },
                    }

                    is_success = True

                else:  # RetryType.ALL
                    summary_prompt = build_prompt_summary(full_text, self.config.language)
                    summary_result = await self._call_llm_with_fallback(summary_prompt, SummaryResult)
                    action_prompt = build_prompt_action_items(full_text, self.config.language)
                    action_result = await self._call_llm_with_fallback(action_prompt, ActionItemsResult)

                    is_success = summary_result is not None and action_result is not None

                    summary_parts = [f"Context\n{summary_result.context}"]

                    if summary_result.key_discussions:
                        summary_parts.append("Key Discussions\n" + "\n".join(summary_result.key_discussions))
                    if summary_result.next_focus:
                        summary_parts.append("Next Focus\n" + "\n".join(summary_result.next_focus))
                    if summary_result.detail:
                        summary_parts.append("Detail\n" + "\n".join(summary_result.detail))
                    
                    summary_data = {
                        "summary": "\n\n".join(summary_parts),
                        "action_items": {
                            item.participant_identity: item.participant_actions
                            for item in action_result.action_items
                        },
                    }

                updated = await self.pg_summary_repo.update_room_summary(room_id, summary_data)
                logger.info(f"Updated summary for room {room_id}")
                if not updated:
                    logger.error(f"Failed to update summary for room {room_id}")
                    return None

                # Notify clients via SSE if summary generation is successful
                if is_success:
                    metadata_channel = MetadataChannel()
                    await metadata_channel.push_room_summary_done(
                        room_id=room_id, room_name=summary_doc.room_name or "Unknown"
                    )

                logger.info(
                    f"Successfully updated summary for room {room_id} and notified clients (success={is_success})"
                )
                return summary_data
            except Exception as e:
                logger.error(f"Failed to retry summary for room {room_id} with type '{retry_type.value}': {e}")
                return None

    async def get_summary_by_room_name(
        self,
        room_name: str,
        start_time: Optional[datetime],
        end_time: Optional[datetime],
        auth: AuthContext
    ) -> Tuple[List[RoomSummaryResponse], int]:
        if not self.pg_summary_repo.connected:
            await self.pg_summary_repo.connect()
        
        if auth.can_view_all_rooms:
            summaries, rooms = await self.pg_summary_repo.get_summary_by_room_name(
                room_name, start_time, end_time
            )
        else:
            summaries, rooms = await self.pg_summary_repo.get_summary_by_room_name(
                room_name, start_time, end_time, auth.user_id
            )

        if not summaries:
            return [], 0

        room_dict = {str(r.id): r for r in rooms}
        summary_models = []

        for summary in summaries:
            summary_dict = {c.name: getattr(summary, c.name) for c in summary.__table__.columns}
            room = room_dict.get(str(summary.room_id))
            summary_dict["created_at"] = room.created_at if room else None
            summary_dict["completed_at"] = room.completed_at if room else None

            if summary_dict.get("room_id") is not None:
                summary_dict["room_id"] = str(summary_dict["room_id"])
            if summary_dict.get("created_at") is not None:
                summary_dict["created_at"] = convert_to_iso_8601(summary_dict["created_at"])
            if summary_dict.get("completed_at") is not None:
                summary_dict["completed_at"] = convert_to_iso_8601(summary_dict["completed_at"])

            summary_models.append(RoomSummaryResponse.model_construct(**summary_dict))

        return summary_models, len(summary_models)

    async def get_summary_by_room_id(
        self,
        room_id: str,
        auth: AuthContext
    ) -> RoomSummaryResponse:
        if not self.pg_summary_repo.connected:
            await self.pg_summary_repo.connect()
        if not self.pg_transcript_repo.connected:
            await self.pg_transcript_repo.connect()

        if not auth.can_view_all_rooms:
            has_access = await self.pg_transcript_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )
        
        summary, room = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        if not summary or not room:
            return RoomSummaryResponse.model_construct()

        summary_dict = {c.name: getattr(summary, c.name) for c in summary.__table__.columns}
        summary_dict["created_at"] = room.created_at
        summary_dict["completed_at"] = room.completed_at

        if summary_dict.get("room_id") is not None:
            summary_dict["room_id"] = str(summary_dict["room_id"])
        if summary_dict.get("created_at") is not None:
            summary_dict["created_at"] = convert_to_iso_8601(summary_dict["created_at"])
        if summary_dict.get("completed_at") is not None:
            summary_dict["completed_at"] = convert_to_iso_8601(summary_dict["completed_at"])

        speech_durations = []
        room_participants_raw = room.participants

        # TODO: Use `Any` type because the `room_participant_raw` data to insert into `room_participants` has complex type
        room_participants: list[dict[str, Any]] = (  # type: ignore[explicit-any]
            room_participants_raw if isinstance(room_participants_raw, list) else []
        )

        for p in room_participants:
            user_id = p.get("participant_identity")
            if user_id and not user_id.startswith("EG_"):
                speech_durations.append({"participant_identity": user_id, "duration": p.get("duration", 0.0)})

        summary_dict["speech_durations"] = speech_durations

        return RoomSummaryResponse.model_construct(**summary_dict)


# Get singleton instance
_summary_service: SummaryService | None = None


def get_summary_service() -> SummaryService:
    global _summary_service
    if _summary_service is None:
        config = get_config()
        pg_transcript_repo = get_pg_transcript_repository()
        pg_summary_repo = get_pg_summary_repository()
        
        primary_llm = create_llm_service(config.summary.provider, config.summary.model)
        
        fallback_llm = None
        if config.summary.fallback_enable:
            fallback_llm = create_llm_service(config.summary.fallback_provider, config.summary.fallback_model)
        
        light_summary_llm = create_llm_service(config.light_summary.provider, config.light_summary.model)
        light_summary_service = LightSummaryService(pg_summary_repo, light_summary_llm)
        _summary_service = SummaryService(
            pg_transcript_repo=pg_transcript_repo,
            pg_summary_repo=pg_summary_repo,
            outbox_repo=get_pg_outbox_repository(),
            light_summary_service=light_summary_service,
            llm_service=primary_llm,
            llm_service_fallback=fallback_llm,
        )
    return _summary_service
