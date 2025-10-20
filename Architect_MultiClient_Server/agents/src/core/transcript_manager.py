import asyncio
import json
from datetime import datetime
from typing import Optional

import os
from livekit import agents

from src.core.bot_websocket_client import BotWebSocketClient
from src.logger import get_logger


class TranscriptManager:
    """Manages transcript entries and forwards them to Bot WebSocket server."""
    
    def __init__(self, ctx: agents.JobContext):
        """Initialize TranscriptManager
        
        Args:
            ctx (agents.JobContext): LiveKit job context containing room info
        """
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        
        # Per-participant incremental sequence for client-side ordering 
        self._seq_by_participant = {}

        # Bot WebSocket configuration
        self.bot_ws_host = os.getenv("BOT_WS_HOST", "bot")
        self.bot_ws_port = os.getenv("BOT_WS_PORT", "8080")

        # Bot WebSocket client (khởi tạo lazy)
        self._bot_ws_client: Optional[BotWebSocketClient] = None
        self._bot_ws_lock = asyncio.Lock()

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
        """Forward transcript to Bot WebSocket
        
        Args:
            text (str): Transcript text 
            participant_identity (str): Participant's identity
            participant_name (str, optional): Display name. Defaults to "Speaker".
            is_final (bool, optional): Whether this is final transcript. Defaults to True.
            segments (list, optional): Transcript segments. Defaults to None.
            language (str, optional): Language code. Defaults to None.
            seq (int, optional): Sequence number. Defaults to None.
        
        Returns:
            bool: True if sent successfully
        """
        try:
            # Forward both interim and final transcripts
            
            # Ensure Bot connection
            if not await self._ensure_bot_connection():
                self.logger.error("Cannot connect to Bot, dropping transcript")
                return False
            
            # Compute sequence if not provided
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current
            
            # Build payload (match Vosk server format). Keep is_final as provided
            payload = {
                "text": text.strip(),
                "is_final": bool(is_final),
                "client_id": participant_identity,
                "session_id": self.session_id,
                "timestamp": datetime.utcnow().isoformat()
            }
            
            # Send to Bot
            success = await self._bot_ws_client.send_transcript(payload)
            
            if success:
                self.logger.debug(f"Sent transcript to Bot: [{participant_identity}] {text[:50]}...")
            else:
                self.logger.warning(f"Failed to send transcript to Bot for {participant_identity}")
            
            return success
            
        except Exception as e:
            self.logger.error(f"Error sending transcript to Bot: {e}")
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
    
    async def cleanup(self):
        """Cleanup Bot WebSocket connection"""
        if self._bot_ws_client:
            await self._bot_ws_client.disconnect()
            self._bot_ws_client = None
            self.logger.info("Bot WebSocket connection cleaned up")

    async def _ensure_bot_connection(self) -> bool:
        """Ensure Bot WebSocket connection is established
        
        Returns:
            bool: True if connected successfully
        """
        async with self._bot_ws_lock:
            if self._bot_ws_client and self._bot_ws_client.connected:
                return True

            # Ensure BotWebSocketClient uses configured host/port
            BotWebSocketClient.BOT_WS_HOST = self.bot_ws_host
            BotWebSocketClient.BOT_WS_PORT = self.bot_ws_port

            # Create new client
            self._bot_ws_client = BotWebSocketClient(
                session_id=self.session_id,
                meeting_code=self.session_id
            )

            # Connect
            success = await self._bot_ws_client.connect()
            if success:
                self.logger.info(f"Bot WebSocket connected for meeting {self.session_id}")
            else:
                self.logger.error(f"Failed to connect Bot WebSocket for meeting {self.session_id}")

            return success