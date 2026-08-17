"""
Transcript lifecycle management - Sequence tracking, data channel publishing
"""
import asyncio
import httpx
from livekit import agents

from src.logger import get_logger
from src.config.application_config import get_config
from src.services.orchestrator_client import OrchestratorClient

class TranscriptManager:
    """Manages transcript entries: sequence tracking, logging"""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize transcript manager and per-participant sequence tracking"""
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        self.count_transcript = 0
        # Load configuration
        self.config = get_config()
        
        # Incremental sequence number for each participant for client ordering
        self._seq_by_participant = {}

        # Session ID = room name (meeting code)
        self.session_id = ctx.room.name
        
        # Buffer for batching transcripts per participant
        # Key: participant_identity, Value: {"texts": [], "timer_task": Task, "last_activity": timestamp}
        self._transcript_buffer = {}
        self._buffer_lock = asyncio.Lock()
        self._orchestrator = OrchestratorClient.get_instance()

        self.logger.info(
            f"TranscriptManager initialized for meeting {self.session_id} "
        )


    
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
        """
        Core transcript processing - sends immediately without buffering
        """
        try:
            # Auto-increment sequence for participant
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current
            
            # Log with FINAL/PARTIAL prefix for visibility
            transcript_type = "FINAL" if is_final else "PARTIAL"
            self.logger.debug(
                f"[{transcript_type}] [{self.session_id}] {participant_name} ({participant_identity}): {text}"
            )

            # Send immediately if has content (fire-and-forget to prevent blocking)
            if text.strip():
                
                success = await self._orchestrator.push_transcript(
                    room_name=self.session_id,
                    text=text,
                    participant_identity=participant_identity,
                    type=transcript_type
                )
            return success
            
        except Exception as e:
            self.logger.error(f"Error tracking transcript: {e}")
            return False
    
    def create_transcription_callback(self, participant_identity: str, participant_name: str):
        """
        Factory to create callback for WebSocket transcription client
        Nemotron server -> callback -> send_transcript_entry()
        """
        async def transcription_callback(text: str, segments: list = None):
            """Handle transcript from Nemotron server (text + optional segments)"""
            if not text or not text.strip():
                return
            
            
            # Server sends final text (no partial)
            is_final = True
            lang = None  # Optional language detection

            await self.send_transcript_entry(
                text=text.strip(),
                participant_identity=participant_identity,
                participant_name=participant_name,
                is_final=is_final,
                segments=segments,
                language=lang,
            )
        
        return transcription_callback
    
    
    async def cleanup(self):
        """Cleanup sequence tracking, flush remaining buffers"""
        self.logger.info("Cleaning up TranscriptManager...")
        
        self._seq_by_participant.clear()
        
        self.logger.info("TranscriptManager cleaned up")
