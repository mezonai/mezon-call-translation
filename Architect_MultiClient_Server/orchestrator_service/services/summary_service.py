"""
Service for generating room summaries
"""
from datetime import datetime
from typing import Optional, Dict, Any

from orchestrator_service.services.mongodb_service import get_mongodb_service
from orchestrator_service.models.summary_models import RoomSummary, SummaryActionItemsResult
from orchestrator_service.config.application_config import get_config
from google import genai

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

class SummaryService:
    """Service to handle room summarization logic"""
    
    def __init__(self):
        self.mongodb = get_mongodb_service()
        self.config = get_config()
        self.genai_client = None
        
        if self.config.llm.gemini_api_key:
            try:
                self.genai_client = genai.Client(api_key=self.config.llm.gemini_api_key)
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")

    def summarize_conversation(self, conversation_text: str) -> SummaryActionItemsResult:
        """
        Summarize conversation using Google Gemini (New SDK).
        Returns a dictionary with 'summary' and 'action_items'.
        """
        if not self.genai_client:
            return {"summary": "Summarization unavailable: Gemini API key not configured or SDK missing.", "action_items": {}}

        try:
            # Using Gemini 2.5 Flash model (or from config)
            model_name = self.config.llm.gemini_model or 'gemini-2.5-flash'
            
            prompt = f"""
You are an AI assistant specialized in summarizing conversations and extracting action items.

The conversation content is formatted as:

    [time] participant_identity: transcript_text

Example:
    [10:05] participant_identity_1: We should migrate Redis next week.
    [10:06] participant_identity_2: I will handle the configuration.

Important rules:

1. The "participant_identity" is the exact identity after the timestamp.
2. When extracting action items, you MUST use the exact participant_identity as provided.
3. Do NOT rename, normalize, translate, or modify participant identities.
4. Only extract action items that are explicitly stated or clearly committed by a participant.
5. Do NOT invent tasks or infer implicit responsibilities.
6. If no action items are mentioned, return an empty list.
7. Preserve the original meaning. Do NOT add new information.
8. Automatically detect the language of the conversation and return the summary in the SAME language.

Your tasks:

1. Provide a concise summary highlighting:
   - Key discussion points
   - Decisions made (if any)

2. Extract and list all actionable tasks/to-dos, grouped by participant_identifier.

Conversation content:
{conversation_text}
            """

            response = self.genai_client.models.generate_content(
                model=model_name,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": SummaryActionItemsResult.model_json_schema(),
                },
            )
            
            summary_data_result = SummaryActionItemsResult.model_validate_json(response.text)
            return summary_data_result

        except Exception as e:
            logger.error(f"Gemini summarization error: {e}")
            return SummaryActionItemsResult(summary=f"An error occurred during summarization: {e}", action_items=[])

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
        
        # 6. Generate Summary via LLM
        summary_data_result = self.summarize_conversation(full_text)
        
        # 7. Create Summary Object
        summary_model = RoomSummary(
            room_id=room_id,
            room_name=room.get("room_name", "Unknown"),
            participants=list(unique_participants),
            summary_data=summary_data_result.model_dump(),
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
            return result
        
        return None

# Singleton
_summary_service = None

def get_summary_service() -> SummaryService:
    global _summary_service
    if _summary_service is None:
        _summary_service = SummaryService()
    return _summary_service
