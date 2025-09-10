import asyncio
import json
import time
from livekit import agents

from src.logger import get_logger



class TranscriptManager:
    """Manages transcript entries and LiveKit data channel communication"""
    
    def __init__(self, ctx: agents.JobContext):
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        # Per-participant incremental sequence for client-side ordering
        self._seq_by_participant = {}
        # Anchor timestamp per participant (first transcript seen)
        self._start_ms_by_participant = {}
    
    async def send_transcript_entry(
        self,
        text: str,
        participant_identity: str,
        participant_name: str = "Speaker",
        is_final: bool = True,
        segments: list | None = None,
        language: str | None = None,
        seq: int | None = None,
    ):
        """Send transcript payload (flat schema) via Data Channel"""
        try:
            # Compute sequence if not provided
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current
            # Anchor timestamp to first time we receive transcript for this participant
            if participant_identity not in self._start_ms_by_participant:
                self._start_ms_by_participant[participant_identity] = int(time.time() * 1000)
            timestamp_ms = self._start_ms_by_participant[participant_identity]

            # Flat schema for flexible client rendering
            transcript_entry = {
                "participantIdentity": participant_identity,
                "participantName": participant_name,
                "seq": seq,
                "isFinal": is_final,
                "language": language,
                "text": text,
                "segments": segments or [],
                "timestamp": timestamp_ms,
            }
            
            transcript_data = {
                "type": "transcript",
                "entry": transcript_entry
            }
            data = json.dumps(transcript_data).encode("utf-8")
            # Only log detailed transcript info at debug level
            self.logger.debug(f"Published transcript: {text[:50]}..." if len(text) > 50 else f"Published transcript: {text}")
            await self.ctx.room.local_participant.publish_data(
                data,
                reliable=True,
                topic="transcript"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending transcript: {e}")
            return False
    
    def create_transcription_callback(self, participant_identity: str, participant_name: str):
        """Create transcription callback for a specific participant"""
        
        async def transcription_callback(text: str, segments: list = None):
            """Handle transcription results from server"""
            if not text or not text.strip():
                return
            
            # Since server only sends text, create a single segment
            if segments is None:
                segments = [{
                    "text": text.strip(),
                    "start": 0.0,
                    "end": 0.0,
                    "completed": True
                }]
            
            # Determine if this is a final transcription
            is_final = True  # Server sends final text, so always final
            
            # Optional: pick language if server attached it in segments (keep None if absent)
            lang = None

            # Prepare convenience text (already provided), forward raw segments to client
            await self.send_transcript_entry(
                text=text.strip(),
                participant_identity=participant_identity,
                participant_name=participant_name,
                is_final=is_final,
                segments=segments,
                language=lang,
            )
        
        return transcription_callback
    
    async def send_welcome_message(self):
        """Send welcome message when agent is ready"""
        await asyncio.sleep(2)  # Wait for stable connection
        await self.send_transcript_entry(
            text="Vosk transcription agent is ready!",
            participant_identity="agent",
            participant_name="Transcription Agent",
            is_final=True
        )