import asyncio
import json
import os
import time
from datetime import datetime
from typing import Optional

import websockets
from livekit import agents

from src.logger import get_logger



class TranscriptManager:
    """Manages transcript entries and forwards them to Bot WebSocket server."""
    
    def __init__(self, ctx: agents.JobContext):
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        # Per-participant incremental sequence for client-side ordering
        self._seq_by_participant = {}
        # Anchor timestamp per participant (first transcript seen)
        self._start_ms_by_participant = {}

        # Bot WebSocket configuration (use env to decouple from LK data channel)
        self.bot_ws_host: str = os.getenv("BOT_WS_HOST", "bot")
        self.bot_ws_port: str = os.getenv("BOT_WS_PORT", "8080")
        self.bot_meeting_code: Optional[str] = "BvDcmJeHg"
        self.bot_user_id: Optional[str] = "1946168514767228928"
        self.bot_name_user_fallback: str = os.getenv("BOT_NAME_USER", "Agent")
        self._bot_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._bot_ws_lock = asyncio.Lock()

        # Kick off connection if config present
        if self.bot_meeting_code and self.bot_user_id:
            asyncio.create_task(self._ensure_bot_connection())
        else:
            self.logger.warning(
                "BOT_MEETING_CODE or BOT_USER_ID not set; Bot WS forwarding disabled"
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
        """Forward transcript to Bot WebSocket using bot/test.js JSON format."""
        try:
            # Compute sequence if not provided (kept for ordering/debug)
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current

            # Ensure bot WS configured
            if not (self.bot_meeting_code and self.bot_user_id):
                self.logger.debug("Bot WS not configured; skipping forward")
                return False

            # Ensure connection
            await self._ensure_bot_connection()
            if not self._bot_ws:
                self.logger.error("Bot WS not connected")
                return False

            # Derive display name
            name_user = participant_name or self.bot_name_user_fallback

            payload = {
                "type": "transcript",
                "meetingCode": self.bot_meeting_code,
                "name_user": name_user,
                "text": text,
                "timestamp": datetime.utcnow().isoformat()
            }

            async with self._bot_ws_lock:
                await self._bot_ws.send(json.dumps(payload))
            return True

        except Exception as e:
            self.logger.error(f"Error sending transcript to Bot WS: {e}")
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

    async def _ensure_bot_connection(self):
        """Ensure a WebSocket connection to the Bot and send register once."""
        if not (self.bot_meeting_code and self.bot_user_id):
            return
        try:
            # Some websocket client objects (different libraries/versions) may not
            # expose a `.closed` attribute. Be defensive: determine if the current
            # _bot_ws appears open; if so, reuse it, otherwise (or unknown) create a
            # new connection.
            def _ws_is_open(ws) -> bool:
                try:
                    # Prefer `.closed` when available (websockets library uses this)
                    closed_attr = getattr(ws, "closed", None)
                    if closed_attr is not None:
                        return not bool(closed_attr)

                    # Some implementations expose `.open`
                    open_attr = getattr(ws, "open", None)
                    if open_attr is not None:
                        return bool(open_attr)

                    # Fallback: if the object has a `send` attribute, assume it's usable
                    if hasattr(ws, "send"):
                        return True
                except Exception:
                    # Any unexpected error treat as not open
                    return False
                return False

            if self._bot_ws and _ws_is_open(self._bot_ws):
                return
            uri = f"ws://{self.bot_ws_host}:{self.bot_ws_port}"
            self._bot_ws = await websockets.connect(uri, ping_interval=20, ping_timeout=20)
            # Send register once connected
            register_payload = {
                "type": "register",
                "meetingCode": self.bot_meeting_code,
                "userId": self.bot_user_id
            }
            await self._bot_ws.send(json.dumps(register_payload))
            self.logger.info(f"Registered to Bot WS for meeting {self.bot_meeting_code} as {self.bot_user_id}")
        except Exception as e:
            self.logger.error(f"Failed to connect/register to Bot WS: {e}")