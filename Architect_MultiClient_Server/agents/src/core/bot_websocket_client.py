import asyncio
import json
import logging
from typing import Optional
from datetime import datetime

import websockets

logger = logging.getLogger("bot_ws_client")


class BotWebSocketClient:
    """Persistent WebSocket client used by Agent to forward transcripts to Bot.

    - Connects to ws://{BOT_WS_HOST}:{BOT_WS_PORT}/ws/agent/{session_id}
    - Maintains a single persistent connection per meeting (agent instance)
    - Auto-reconnects with exponential backoff (max 5 attempts)
    - Does not buffer outgoing transcripts when offline (they are dropped)
    """

    BOT_WS_HOST = "bot"
    BOT_WS_PORT = "8080"

    def __init__(self, session_id: str, meeting_code: str):
        self.session_id = session_id
        self.meeting_code = meeting_code
        self.uri = f"ws://{self.BOT_WS_HOST}:{self.BOT_WS_PORT}/ws/agent/BvDcmJeHg" #{session_id}
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.reconnecting = False
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Establish connection to Bot WebSocket server.

        Returns True on success, False otherwise.
        """
        async with self._connect_lock:
            try:
                logger.info(f"Connecting to Bot at {self.uri} for meeting {self.meeting_code}")
                # Use a timeout for connect
                self.websocket = await asyncio.wait_for(websockets.connect(self.uri), timeout=10.0)
                self.connected = True
                logger.info(f"Connected to Bot for meeting {self.meeting_code}")

                # Start a background task to monitor connection
                asyncio.create_task(self._recv_loop())
                return True
            except asyncio.TimeoutError:
                logger.warning(f"Timeout connecting to Bot for meeting {self.meeting_code}")
            except Exception as e:
                logger.error(f"Failed to connect to Bot for meeting {self.meeting_code}: {e}")

            self.connected = False
            return False

    async def _recv_loop(self):
        """Background reader to detect connection closure and start reconnects."""
        if not self.websocket:
            return

        try:
            async for _ in self.websocket:
                # We don't expect messages from Bot; ignore but keep loop to detect closes
                pass
        except Exception as e:
            logger.debug(f"Bot websocket recv loop error for {self.meeting_code}: {e}")
        finally:
            if self.connected:
                # Connection lost
                logger.warning(f"Bot websocket disconnected for meeting {self.meeting_code}")
            self.connected = False
            # Trigger reconnect attempts
            asyncio.create_task(self.reconnect())

    async def send_transcript(self, payload: dict) -> bool:
        """Send transcript payload to Bot. Returns True if sent.

        Drops payload if connection is not available.
        """
        if not self.connected or not self.websocket:
            logger.warning(f"Not connected to Bot, dropping transcript for meeting {self.meeting_code}")
            return False

        try:
            # Ensure payload contains timestamp
            if "timestamp" not in payload:
                payload["timestamp"] = datetime.utcnow().isoformat()

            await asyncio.wait_for(self.websocket.send(json.dumps(payload)), timeout=10.0)
            return True
        except asyncio.TimeoutError:
            logger.warning(f"Timeout sending transcript to Bot for meeting {self.meeting_code}")
        except Exception as e:
            logger.error(f"Error sending transcript to Bot for meeting {self.meeting_code}: {e}")

        return False

    async def reconnect(self) -> bool:
        """Attempt to reconnect with exponential backoff (1,2,4,8,16s). Max 5 attempts."""
        if self.reconnecting:
            return False

        self.reconnecting = True
        attempts = 5
        backoff = 1
        success = False

        for n in range(1, attempts + 1):
            logger.info(f"Reconnecting to Bot (attempt {n}/{attempts})")
            try:
                ok = await self.connect()
                if ok:
                    success = True
                    break
            except Exception as e:
                logger.debug(f"Reconnect attempt {n} exception: {e}")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 16)

        if not success:
            logger.error(f"Failed to reconnect to Bot after {attempts} attempts for meeting {self.meeting_code}")
            self.connected = False

        self.reconnecting = False
        return success

    async def disconnect(self):
        """Gracefully close websocket connection."""
        if self.websocket:
            try:
                await asyncio.wait_for(self.websocket.close(), timeout=5.0)
            except Exception as e:
                logger.debug(f"Error while closing websocket for {self.meeting_code}: {e}")

        self.websocket = None
        self.connected = False
        logger.info(f"Disconnected from Bot for meeting {self.meeting_code}")
