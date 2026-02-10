"""
Summary Processor - Generate room summaries using AI

Processes summary generation tasks:
1. Collect all transcripts from room
2. Format conversation text
3. Call Gemini AI for summarization
4. Save summary to MongoDB
"""

import logging
from datetime import datetime
from typing import Optional, Dict, Any, List
import json

from stt_service.service.mongodb_service import get_mongodb_service
from stt_service.models.summary_models import RoomSummary
from stt_service.config.app_config import get_config

logger = logging.getLogger(__name__)


class SummaryProcessor:
    """Service to handle room summarization logic"""
    
    _instance: Optional['SummaryProcessor'] = None
    
    def __init__(self):
        """Initialize summary processor."""
        self.mongodb = get_mongodb_service()
        self.config = get_config()
        self.genai_client = None
        
        # Initialize Gemini client if API key is configured
        if self.config.llm.gemini_api_key:
            try:
                from google import genai
                self.genai_client = genai.Client(api_key=self.config.llm.gemini_api_key)
                logger.info(f"✅ Gemini client initialized (model: {self.config.llm.gemini_model})")
            except Exception as e:
                logger.error(f"Failed to initialize Gemini client: {e}")
        else:
            logger.warning("⚠️ Gemini API key not configured - summary generation will be unavailable")
    
    @classmethod
    def get_instance(cls) -> 'SummaryProcessor':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _summarize_conversation(self, conversation_text: str) -> Dict[str, Any]:
        """
        Summarize conversation using Google Gemini.
        
        Args:
            conversation_text: Full formatted conversation text
            
        Returns:
            Dictionary with 'summary' and 'action_items' keys
        """
        if not self.genai_client:
            return {
                "summary": "Summarization unavailable: Gemini API key not configured or SDK missing.",
                "action_items": {}
            }

        try:
            model_name = self.config.llm.gemini_model or 'gemini-2.0-flash'
            
            prompt = f"""
            You are an AI assistant skilled at summarizing information.
            Please summarize the following conversation concisely, highlighting key points and conclusions (if any).
            
            Also, please extract and list all actionable tasks/to-dos mentioned in the conversation for each person.

            Return the output in the following JSON format:
            {{
                "summary": "Summary content",
                "action_items": {{
                    "user_name1": ["task1", "task2"],
                    "user_name2": ["task1", "task2"]
                }}
            }}
            
            If there are no action items, "action_items" should be an empty object {{}}.

            Important: Automatically detect the language of the conversation and return the summary in THAT SAME LANGUAGE.

            Conversation content:
            {conversation_text}
            """

            response = self.genai_client.models.generate_content(
                model=model_name,
                contents=prompt
            )
            
            text_response = response.text
            
            # Clean up markdown code blocks if present
            if text_response.startswith("```json"):
                text_response = text_response[7:]
            elif text_response.startswith("```"):
                text_response = text_response[3:]
            if text_response.endswith("```"):
                text_response = text_response[:-3]
            
            try:
                # Parse JSON response
                return json.loads(text_response.strip())
            except json.JSONDecodeError:
                logger.error(f"Failed to parse Gemini response as JSON: {text_response}")
                return {"summary": text_response, "action_items": {}}

        except Exception as e:
            logger.error(f"Gemini summarization error: {e}", exc_info=True)
            return {
                "summary": f"An error occurred during summarization: {e}",
                "action_items": {}
            }

    async def process_summary(self, room_id: str) -> Optional[Dict[str, Any]]:
        """
        Generate a summary for the given room_id.
        
        This is the main entry point called by the summary queue.
        
        Strategy:
        1. Verify room exists
        2. Get all tracks for the room
        3. Collect all transcript segments
        4. Sort by absolute timestamp
        5. Format conversation text
        6. Generate summary via Gemini AI
        7. Create and save summary object
        
        Args:
            room_id: Room identifier (string)
            
        Returns:
            Summary document dictionary if successful, None otherwise
        """
        logger.info(f"📝 Generating summary for room {room_id}")
        
        # Check MongoDB connection first
        if not self.mongodb.connected:
            logger.error(f"❌ MongoDB not connected - cannot process summary for room {room_id}")
            logger.info(f"💡 Ensure MongoDB is running and connection is established")
            return None
        
        try:
            from bson import ObjectId
            
            # Convert room_id string to ObjectId
            try:
                room_ref_id = ObjectId(room_id)
                logger.debug(f"Processing summary for room {room_id} (ObjectId: {room_ref_id})")
            except Exception as e:
                logger.error(f"❌ Invalid room_id format: {room_id} - {e}")
                logger.info(f"💡 room_id must be 24 character hex string (MongoDB ObjectId format)")
                return None
            
            # 1. Verify room exists
            room = await self.mongodb.get_room_by_id(room_ref_id)
            if not room:
                logger.warning(f"❌ Room not found in database: {room_id} (ObjectId: {room_ref_id})")
                logger.info(f"💡 This may be normal if room was just created or hasn't been synced yet")
                return None
            
            # 2. Get all tracks for the room
            tracks = await self.mongodb.get_room_tracks(room_ref_id)
            if not tracks:
                logger.warning(f"❌ No tracks found for room {room_id}")
                logger.info(f"💡 Room exists but has no audio tracks yet - may be empty room or tracks not saved")
                logger.info(f"   Room info: name={room.get('room_name')}, created={room.get('created_at')}")
                return None

            # 3. Collect all transcript segments
            all_segments = []
            
            for track in tracks:
                try:
                    track_id = str(track["_id"])
                    participant = track.get("participant_identity", "Unknown")
                    
                    # Fetch chunks for this track (sorted by chunk_index)
                    chunks = await self.mongodb.get_track_chunks(track_id)
                    
                    # Get track audio info for timestamp calculation
                    audio_info = track.get("audio_info", {})
                    start_ns_str = audio_info.get("started_at_ns", "0")
                    try:
                        track_start_ns = int(start_ns_str)
                    except (ValueError, TypeError):
                        track_start_ns = 0

                    # Process each chunk's segments
                    for chunk in chunks:
                        segments = chunk.get("segments", [])
                        for seg in segments:
                            # Add participant info
                            seg["participant"] = participant
                            
                            # Calculate absolute timestamp
                            seg_start_sec = seg.get("start", 0.0) or 0.0
                            total_ns = track_start_ns + int(seg_start_sec * 1_000_000_000)
                            seg["absolute_start_ns"] = total_ns
                            
                            all_segments.append(seg)
                            
                except Exception as e:
                    logger.error(f"Error processing track {track.get('_id')}: {e}")
                    continue

            if not all_segments:
                logger.warning(f"❌ No transcript segments found for room {room_id}")
                logger.info(f"💡 Room has {len(tracks)} track(s) but no transcript segments")
                logger.info(f"   This is normal for rooms with no speech or background noise only")
                return None

            # 4. Sort by absolute timestamp
            all_segments.sort(key=lambda x: x.get("absolute_start_ns", 0))
            
            # 5. Format conversation text
            text_lines = []
            unique_participants = set()
            last_participant = None

            for seg in all_segments:
                participant = seg.get("participant", "Unknown")
                if participant != "Unknown":
                    unique_participants.add(participant)
                
                text = seg.get("text", "").strip()
                
                if text:
                    if participant == last_participant:
                        # Same speaker, continue text
                        text_lines.append(text)
                    else:
                        # New speaker, add timestamp and name
                        dt = datetime.fromtimestamp(seg["absolute_start_ns"] / 1_000_000_000)
                        time_str = dt.strftime("%H:%M:%S")
                        text_lines.append(f"[{time_str}] {participant}: {text}")
                    
                    last_participant = participant
            
            full_text = "\n".join(text_lines)
            
            logger.info(
                f"Collected {len(all_segments)} segments from {len(tracks)} tracks "
                f"for room {room_id}"
            )
            
            # 6. Generate summary via Gemini AI
            logger.info(f"Calling Gemini AI for summary generation...")
            summary_data_result = self._summarize_conversation(full_text)
            
            # 7. Create summary object
            summary_model = RoomSummary(
                room_id=room_id,
                room_name=room.get("room_name", "Unknown"),
                participants=list(unique_participants),
                summary_data=summary_data_result,
                full_text=full_text,
                created_at=datetime.utcnow(),
                total_segments=len(all_segments)
            )
            
            # 8. Save to MongoDB
            saved_id = await self.mongodb.save_room_summary(summary_model.model_dump())
            
            if saved_id:
                logger.info(
                    f"✅ Generated summary for room {room_id} "
                    f"(summary_id: {saved_id}, segments: {len(all_segments)})"
                )
                result = summary_model.model_dump()
                result["_id"] = saved_id
                return result
            else:
                logger.error(f"Failed to save summary for room {room_id}")
                return None
            
        except Exception as e:
            logger.error(f"Error generating summary for room {room_id}: {e}", exc_info=True)
            return None


def get_summary_processor() -> SummaryProcessor:
    """
    Get the singleton summary processor instance.
    
    Returns:
        SummaryProcessor instance
    """
    return SummaryProcessor.get_instance()
