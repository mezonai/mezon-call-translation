import asyncio
from livekit import agents

from src.logger import get_logger


class TranscriptManager:
    """Manages transcript entries for tracking and logging purposes."""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize TranscriptManager
        
        Args:
            ctx (agents.JobContext): LiveKit job context containing room info
        """
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        
        # Per-participant incremental sequence for client-side ordering 
        self._seq_by_participant = {}

        # Session ID = meeting code = room name
        self.session_id = ctx.room.name

        self.logger.info(f"TranscriptManager initialized for meeting {self.session_id}")
    
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
        """Log and track transcript entry (no longer forwarded to external services)
        
        Args:
            text (str): Transcript text 
            participant_identity (str): Participant's identity
            participant_name (str, optional): Display name. Defaults to "Speaker".
            is_final (bool, optional): Whether this is final transcript. Defaults to True.
            segments (list, optional): Transcript segments. Defaults to None.
            language (str, optional): Language code. Defaults to None.
            seq (int, optional): Sequence number. Defaults to None.
        
        Returns:
            bool: Always returns True (for backward compatibility)
        """
        try:
            # Compute sequence if not provided
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current
            
            # Log transcript entry for tracking
            transcript_type = "FINAL" if is_final else "PARTIAL"
            self.logger.info(
                f"[{transcript_type}] [{self.session_id}] {participant_name} ({participant_identity}): {text[:100]}..."
            )
            
            # Track internally but don't forward anywhere
            self.logger.debug(
                f"Transcript tracked - seq={seq}, is_final={is_final}, "
                f"client={participant_identity}, session={self.session_id}"
            )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error tracking transcript: {e}")
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
        self.logger.info("Vosk transcription agent is ready!")
    
    async def cleanup(self):
        """Cleanup internal state"""
        self._seq_by_participant.clear()
        self.logger.info("TranscriptManager cleaned up")