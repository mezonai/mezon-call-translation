"""
Service for generating room summaries
"""
from datetime import datetime
from typing import Optional, Dict, Any

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.services.mongodb_service import MongoDBService
from orchestrator_service.services.llm.factory import create_llm_service
from orchestrator_service.models.summary_models import RoomSummary
from orchestrator_service.config.application_config import get_config

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

class SummaryService:
    """Service to handle room summarization logic"""

    def __init__(self):
        self.mongodb = MongoDBService()
        self.config = get_config()
        # Create LLM service based on configured provider
        self.llm_service = create_llm_service(self.config.llm)
        logger.info(f"SummaryService initialized with LLM provider: {self.config.llm.provider}")

    async def generate_summary(self, room_id: str) -> Optional[Dict[str, Any]]:
        """
        Generate a summary for the given room_id.
        
        Currently implements a concatenation strategy:
        1. Verify room exists.
        2. Get all tracks.
        3. Collect all segments.
        4. Sort by absolute time.
        5. Collect Full Text and Participants.
        6. Generate Summary via LLM.
        7. Create Summary Object.
        8. Save summary to DB.
        """
        # Ensure MongoDB is connected
        if not self.mongodb.connected:
            await self.mongodb.connect()
        
        # 1. Verify room exists
        logger.info(f"Generating summary for room {repr(room_id)}")
        room = await self.mongodb.get_room_by_id(room_id)
        if not room:
            logger.warning(f"Room not found: {room_id}")
            return None   
        
        # 2. Get all tracks
        tracks = await self.mongodb.get_tracks_by_room(room_id)
        if not tracks:
            logger.warning(f"No tracks found for room {room_id}")
            return None

        all_segments = []
        
        # 3. Collect all segments
        for track in tracks:
            try:
                track_id = str(track["_id"])
                participant = track.get("participant_identity", "Unknown")
                
                # Fetch chunks
                chunks = await self.mongodb.get_chunks_by_track(track_id, sorted_by_index=True)
                
                audio_info = track.get("audio_info", {})
                start_ns_str = audio_info.get("started_at_ns", "0")
                try:
                    track_start_ns = int(start_ns_str)
                except (ValueError, TypeError):
                    track_start_ns = 0

                for chunk in chunks:
                    segments = chunk.get("segments", [])
                    for seg in segments:
                        # Append participant info for context
                        seg["participant"] = participant
                        
                        # Calculate absolute timestamp
                        # segment.start is in seconds
                        seg_start_sec = seg.get("start", 0.0) or 0.0
                        total_ns = track_start_ns + int(seg_start_sec * 1_000_000_000)
                        seg["absolute_start_ns"] = total_ns
                        
                        all_segments.append(seg)
            except Exception as e:
                logger.error(f"Error processing track {track.get('_id')}: {e}")
                continue

        if not all_segments:
            logger.warning(f"No transcript segments found for room {room_id}")
            return None

        # 4. Sort by absolute time
        all_segments.sort(key=lambda x: x.get("absolute_start_ns", 0))
        
        # 5. Collect Full Text and Participants
        text_lines = []
        unique_participants = set()
        
        last_participant = None

        for seg in all_segments:
            participant = seg.get("participant", "Unknown")
            if participant != "Unknown":
                unique_participants.add(participant)
            
            text = seg.get("text", "").strip() # Use text from segment
            
            if text:
                if participant == last_participant:
                    text_lines.append(text)
                else:
                    dt = datetime.fromtimestamp(seg["absolute_start_ns"] / 1_000_000_000)
                    time_str = dt.strftime("%H:%M:%S")
                    text_lines.append(f"[{time_str}] {participant}: {text}")
                
                last_participant = participant
        
        full_text = "\n".join(text_lines)

        # 6. Generate Summary via LLM (uses configured provider)
        summary_data_result = await self.llm_service.summarize_conversation(conversation_text = full_text, language=self.config.llm.language)
        action_items = summary_data_result.action_items
        action_items_dict = {action_item.participant_identity: action_item.participant_actions for action_item in action_items}
        summary_data = {
            "summary": summary_data_result.summary,
            "action_items": action_items_dict
        }
        
        # 7. Create Summary Object
        summary_model = RoomSummary(
            room_id=room_id,
            room_name=room.get("room_name", "Unknown"),
            participants=list(unique_participants),
            summary_data=summary_data,
            full_text=full_text,
            created_at= datetime.utcnow(),
            total_segments=len(all_segments)
        )
        
        # 8. Save to DB
        saved_id = await self.mongodb.save_room_summary(summary_model.model_dump())
        if saved_id:
            logger.info(f"Generated summary for room {room_id} (ID: {saved_id})")
            result = summary_model.model_dump()
            result["_id"] = saved_id
            
            #9. Notify clients via SSE if summary generation is successful
            metadata_channal  = MetadataChannel()
            await metadata_channal.push_room_summary_done(
                room_id=room_id,
                room_name=room.get("room_name", "Unknown")
            )
            return result
 
        return None

# Singleton
_summary_service = None

def get_summary_service() -> SummaryService:
    global _summary_service
    if _summary_service is None:
        _summary_service = SummaryService()
    return _summary_service
