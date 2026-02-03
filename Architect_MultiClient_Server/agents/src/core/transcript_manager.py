"""
Transcript lifecycle management - Sequence tracking, data channel publishing
"""
import asyncio
import httpx
from livekit import agents

from src.logger import get_logger
from src.config.application_config import get_config

class TranscriptManager:
    """Manages transcript entries: sequence tracking, logging"""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize transcript manager and per-participant sequence tracking"""
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        
        # Load configuration
        config = get_config()
        self.silence_timeout = config.transcript.silence_timeout
        
        # Incremental sequence number for each participant for client ordering
        self._seq_by_participant = {}

        # Session ID = room name (meeting code)
        self.session_id = ctx.room.name
        
        # Buffer for batching transcripts per participant
        # Key: participant_identity, Value: {"texts": [], "timer_task": Task, "last_activity": timestamp}
        self._transcript_buffer = {}
        self._buffer_lock = asyncio.Lock()

        self.logger.info(
            f"TranscriptManager initialized for meeting {self.session_id} "
            f"silence_timeout={self.silence_timeout}s)"
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
            self.logger.info(
                f"[{transcript_type}] [{self.session_id}] {participant_name} ({participant_identity}): {text}"
            )
            
            # Send immediately if final and has content
            if is_final and text.strip():
                await self._send_to_server(
                    text=text.strip(),
                    participant_identity=participant_identity,
                    participant_name=participant_name,
                    seq=seq
                )
            # Send immediately if final and has content
            if is_final and text.strip():
                await self._send_to_server(
                    text=text.strip(),
                    participant_identity=participant_identity,
                    participant_name=participant_name,
                    seq=seq
                )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error tracking transcript: {e}")
            return False
    
    async def _send_to_server(
        self,
        text: str,
        participant_identity: str,
        participant_name: str,
        seq: int
        participant_name: str,
        seq: int
    ):
        """Send transcript to API server"""
        try:
            room_name = self.ctx.room.name
            config = get_config()
            port = config.server.port
            api_url = f"http://localhost:{port}/api/push_message"
            
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    api_url,
                    json={"room_name": room_name, "message": text},
                    timeout=5.0
                    timeout=5.0
                )
                self.logger.info(
                    f"[API] Pushed to queue via API (room={room_name}): "
                    f"{text[:50]}{'...' if len(text) > 50 else ''}, "
                    f"status={resp.status_code}"
                )
        except Exception as e:
            self.logger.error(f"[API] Failed to push text via API: {e}")
    
    def create_transcription_callback(self, participant_identity: str, participant_name: str):
        """
        Factory to create callback for WebSocket transcription client
        Vosk server -> callback -> send_transcript_entry()
        """
        async def transcription_callback(text: str, segments: list = None):
            """Handle transcript from Vosk server (text + optional segments)"""
            if not text or not text.strip():
                return
            
            # Server only sends text, create default segment
            if segments is None:
                segments = [{
                    "text": text.strip(),
                    "start": 0.0,
                    "end": 0.0,
                    "completed": True
                }]
            
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
    
    async def send_welcome_message(self):
        """Send welcome message when agent is ready (optional)"""
        import asyncio
        import asyncio
        await asyncio.sleep(2)
        self.logger.info("Vosk transcription agent is ready!")
    
    async def cleanup(self):
        """Cleanup sequence tracking, flush remaining buffers"""
        self.logger.info("Cleaning up TranscriptManager...")
        
        self._seq_by_participant.clear()
        
        self.logger.info("TranscriptManager cleaned up")