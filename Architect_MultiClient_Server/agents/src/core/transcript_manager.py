"""
Transcript lifecycle management - Sequence tracking, MongoDB persistence, data channel publishing
"""
import asyncio
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
        self.silence_timeout = config.transcript.silence_timeout
        self.enable_mongodb = config.transcript.enable_mongodb
        
        # Incremental sequence number for each participant for client ordering
        self._seq_by_participant = {}

        # Session ID = room name (meeting code)
        self.session_id = ctx.room.name
        
        # MongoDB service (singleton pattern)
        self.mongodb = get_mongodb_service()
        
        # Buffer for batching transcripts per participant
        # Key: participant_identity, Value: {"texts": [], "timer_task": Task, "last_activity": timestamp}
        self._transcript_buffer = {}
        self._buffer_lock = asyncio.Lock()

        self.logger.info(
            f"TranscriptManager initialized for meeting {self.session_id} "
            f"(MongoDB: {'enabled' if self.enable_mongodb else 'disabled'}, "
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
        Core transcript processing with silence-based batching:
        - When receiving final transcript: buffer it and start/reset 5s timer
        - When receiving any text (final or partial): reset the timer
        - After 5s of silence (no new text): send all buffered texts to server
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
            
            # Handle buffering and timer logic
            async with self._buffer_lock:
                # Initialize buffer for participant if not exists
                if participant_identity not in self._transcript_buffer:
                    self._transcript_buffer[participant_identity] = {
                        "texts": [],
                        "timer_task": None,
                        "participant_name": participant_name,
                        "last_seq": seq
                    }
                
                buffer = self._transcript_buffer[participant_identity]
                
                # Cancel existing timer (reset timeout on any text received)
                if buffer["timer_task"] and not buffer["timer_task"].done():
                    buffer["timer_task"].cancel()
                    try:
                        await buffer["timer_task"]
                    except asyncio.CancelledError:
                        pass
                    self.logger.debug(f"[BUFFER] Timer reset for {participant_identity}")
                
                # Only buffer final transcripts for sending
                if is_final and text.strip():
                    buffer["texts"].append(text.strip())
                    buffer["last_seq"] = seq
                    self.logger.debug(
                        f"[BUFFER] Added text for {participant_identity}, "
                        f"buffer_size={len(buffer['texts'])}"
                    )
                
                # Start new timer (will trigger flush after silence_timeout seconds)
                if buffer["texts"]:  # Only start timer if there's something to send
                    buffer["timer_task"] = asyncio.create_task(
                        self._flush_after_silence(participant_identity)
                    )
                    self.logger.debug(
                        f"[BUFFER] Started {self.silence_timeout}s timer for {participant_identity}"
                    )
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error tracking transcript: {e}")
            return False
    
    async def _flush_after_silence(self, participant_identity: str):
        """
        Wait for silence_timeout seconds then flush buffered transcripts.
        This task gets cancelled if new text arrives before timeout.
        """
        try:
            await asyncio.sleep(self.silence_timeout)
            
            # Timeout reached - flush buffer
            async with self._buffer_lock:
                if participant_identity not in self._transcript_buffer:
                    return
                
                buffer = self._transcript_buffer[participant_identity]
                texts = buffer["texts"]
                participant_name = buffer["participant_name"]
                
                if not texts:
                    return
                
                # Combine all buffered texts
                combined_text = " ".join(texts)
                buffer_count = len(texts)
                
                # Clear buffer
                buffer["texts"] = []
                buffer["timer_task"] = None
            
            self.logger.info(
                f"[FLUSH] {participant_identity}: Sending {buffer_count} buffered texts "
                f"after {self.silence_timeout}s silence"
            )
            
            # Send combined text to server
            await self._send_to_server(
                text=combined_text,
                participant_identity=participant_identity,
                participant_name=participant_name
            )
            
        except asyncio.CancelledError:
            # Timer was cancelled because new text arrived - this is expected
            pass
        except Exception as e:
            self.logger.error(f"Error flushing transcript buffer: {e}")
    
    async def _send_to_server(
        self,
        text: str,
        participant_identity: str,
        participant_name: str
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
                    timeout=5.0  # Increased timeout for combined text
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
            seq = self._seq_by_participant.get(participant_identity, 0)
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
                    "batched": True
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
        await asyncio.sleep(2)
        self.logger.info("Vosk transcription agent is ready!")
    
    async def cleanup(self):
        """Cleanup sequence tracking, flush remaining buffers, and close MongoDB connection"""
        self.logger.info("Cleaning up TranscriptManager...")
        
        # Flush all remaining buffered transcripts before cleanup
        async with self._buffer_lock:
            for participant_identity, buffer in self._transcript_buffer.items():
                # Cancel any pending timers
                if buffer["timer_task"] and not buffer["timer_task"].done():
                    buffer["timer_task"].cancel()
                    try:
                        await buffer["timer_task"]
                    except asyncio.CancelledError:
                        pass
                
                # Send remaining buffered texts
                if buffer["texts"]:
                    combined_text = " ".join(buffer["texts"])
                    self.logger.info(
                        f"[CLEANUP] Flushing {len(buffer['texts'])} remaining texts "
                        f"for {participant_identity}"
                    )
                    await self._send_to_server(
                        text=combined_text,
                        participant_identity=participant_identity,
                        participant_name=buffer["participant_name"]
                    )
            
            self._transcript_buffer.clear()
        
        self._seq_by_participant.clear()
        
        # Disconnect MongoDB connection pool
        if self.enable_mongodb:
            await self.mongodb.disconnect()
        
        self.logger.info("TranscriptManager cleaned up")