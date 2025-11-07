"""
Transcript lifecycle management - Sequence tracking, MongoDB persistence, data channel publishing
"""
import asyncio
import os
from livekit import agents

from src.logger import get_logger
from src.services.mongodb_service import get_mongodb_service


class TranscriptManager:
    """Manages transcript entries: sequence tracking, logging, MongoDB storage"""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize transcript manager with MongoDB and per-participant sequence tracking"""
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        
        # Incremental sequence number for each participant for client ordering
        self._seq_by_participant = {}

        # Session ID = room name (meeting code)
        self.session_id = ctx.room.name
        
        # MongoDB service (singleton pattern)
        self.mongodb = get_mongodb_service()
        self.enable_mongodb = False

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
        Core transcript processing:
        1. Compute sequence number (per-participant incremental)
        2. Log transcript (FINAL vs PARTIAL)
        3. Save to MongoDB (if enabled)
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
            
            # MongoDB persistence (async, does not block main flow)
            if self.enable_mongodb and is_final:
                doc_id = await self.mongodb.save_transcript(
                    session_id=self.session_id,
                    participant_identity=participant_identity,
                    participant_name=participant_name,
                    text=text,
                    is_final=is_final,
                    segments=segments,
                    language=language,
                    seq=seq,
                    metadata={
                        "room_name": self.ctx.room.name,
                        "agent_name": "Vosk-Transcription-Agent"
                    }
                )
                
                if doc_id:
                    self.logger.debug(f"💾 Saved to MongoDB: doc_id={doc_id}")
                else:
                    self.logger.warning("Failed to save transcript to MongoDB")
            else:
                self.logger.debug("MongoDB disabled, transcript only logged")
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error tracking transcript: {e}")
            return False
    
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
        await asyncio.sleep(2)
        self.logger.info("Vosk transcription agent is ready!")
    
    async def cleanup(self):
        """Cleanup sequence tracking and MongoDB connection"""
        self._seq_by_participant.clear()
        
        # Disconnect MongoDB connection pool
        if self.enable_mongodb:
            await self.mongodb.disconnect()
        
        self.logger.info("TranscriptManager cleaned up")