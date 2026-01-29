"""
Transcript lifecycle management - Sequence tracking, MongoDB persistence, data channel publishing
"""
import os
import httpx
from livekit import agents

from src.logger import get_logger
from src.services.mongodb_service import get_mongodb_service
from src.config import get_config

class TranscriptManager:
    """Manages transcript entries: sequence tracking, logging, MongoDB storage"""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize transcript manager with MongoDB and per-participant sequence tracking"""
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        
        # Load configuration
        config = get_config()
        self.enable_mongodb = config.transcript.enable_mongodb
        
        # Incremental sequence number for each participant for client ordering
        self._seq_by_participant = {}

        # Session ID = room name (meeting code)
        self.session_id = ctx.room.name
        
        # MongoDB service (singleton pattern)
        self.mongodb = get_mongodb_service()

        self.logger.info(
            f"TranscriptManager initialized for meeting {self.session_id} "
            f"(MongoDB: {'enabled' if self.enable_mongodb else 'disabled'})"
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
    ):
        """Send transcript to API server and optionally MongoDB"""
        try:
            room_name = self.ctx.room.name
            port = int(os.environ.get("PORT_AGENT", "8002"))
            api_url = f"http://localhost:{port}/api/push_message"
            
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    api_url,
                    json={"room_name": room_name, "message": text},
                    timeout=5.0
                )
                self.logger.info(
                    f"[API] Pushed to queue via API (room={room_name}): "
                    f"{text[:50]}{'...' if len(text) > 50 else ''}, "
                    f"status={resp.status_code}"
                )
        except Exception as e:
            self.logger.error(f"[API] Failed to push text via API: {e}")

        # MongoDB persistence
        if self.enable_mongodb:
            doc_id = await self.mongodb.save_transcript(
                session_id=self.session_id,
                participant_identity=participant_identity,
                participant_name=participant_name,
                text=text,
                is_final=True,
                segments=None,
                language=None,
                seq=seq,
                metadata={
                    "room_name": self.ctx.room.name,
                    "agent_name": "Vosk-Transcription-Agent",
                }
            )
            if doc_id:
                self.logger.info(f"💾 Saved to MongoDB: doc_id={doc_id}")
            else:
                self.logger.warning("Failed to save transcript to MongoDB")
    
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
        await asyncio.sleep(2)
        self.logger.info("Vosk transcription agent is ready!")
    
    async def cleanup(self):
        """Cleanup sequence tracking and close MongoDB connection"""
        self.logger.info("Cleaning up TranscriptManager...")
        
        self._seq_by_participant.clear()
        
        # Disconnect MongoDB connection pool
        if self.enable_mongodb:
            await self.mongodb.disconnect()
        
        self.logger.info("TranscriptManager cleaned up")