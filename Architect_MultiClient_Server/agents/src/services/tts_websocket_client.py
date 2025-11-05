"""
TTS WebSocket Client Service - Communication with TTS server
Integrated with agent logging system
"""
import asyncio
import json
import time
from typing import Optional, Callable, Awaitable
import websockets

from ..logger import get_logger

logger = get_logger(__name__)


class TTSWebSocketClient:
    """
    WebSocket client for TTS server communication
    Handles connection, message receiving, and status updates
    """
    
    def __init__(
        self,
        session_id: str,
        ws_url: Optional[str] = None,
        on_text_received: Optional[Callable[[str], Awaitable[None]]] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0
    ):
        """
        Initialize WebSocket client
        
        Args:
            session_id: Unique session identifier
            ws_url: WebSocket server URL (default: ws://localhost:8089/ws/tts/{session_id})
            on_text_received: Async callback when text is received
            max_retries: Maximum connection retry attempts
            retry_delay: Delay between retries in seconds
        """
        self.session_id = session_id
        self.ws_url = ws_url or f"ws://localhost:8089/ws/tts/{session_id}"
        self.on_text_received = on_text_received
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.is_running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._disconnected_event = asyncio.Event()
        
        logger.info(f"TTSWebSocketClient initialized (session={session_id}, url={self.ws_url})")
    
    async def connect(self) -> bool:
        """
        Connect to WebSocket server with retry logic
        
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(f"Connecting to TTS WebSocket server (attempt {attempt}/{self.max_retries})...")
                
                self.websocket = await websockets.connect(
                    self.ws_url,
                    ping_interval=20,
                    ping_timeout=10
                )
                
                self.is_connected = True
                self.is_running = True
                self._disconnected_event.clear()
                
                logger.info(f"✅ Connected to TTS WebSocket: {self.ws_url}")
                
                # Start receiving messages
                self._receive_task = asyncio.create_task(self._receive_loop())
                
                return True
                
            except Exception as e:
                logger.warning(f"Connection attempt {attempt}/{self.max_retries} failed: {e}")
                
                if attempt < self.max_retries:
                    delay = self.retry_delay * attempt  # Exponential backoff
                    logger.info(f"Retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"Failed to connect after {self.max_retries} attempts")
                    self._print_troubleshooting_guide()
        
        return False
    
    def _print_troubleshooting_guide(self):
        """Print troubleshooting information"""
        logger.error("=" * 60)
        logger.error("TTS WebSocket Connection Failed - Troubleshooting:")
        logger.error(f"  1. Verify server is running:")
        logger.error(f"     python test_tts_websocket_server.py 8089")
        logger.error(f"  2. Check URL: {self.ws_url}")
        logger.error(f"  3. Set custom URL via environment:")
        logger.error(f"     TTS_WS_URL='ws://localhost:PORT/ws/tts/{{session_id}}'")
        logger.error(f"  4. Check firewall/network settings")
        logger.error("=" * 60)
    
    async def _receive_loop(self):
        """Receive and process messages from WebSocket server"""
        logger.info("📡 Listening for TTS messages from server...")
        
        try:
            async for message in self.websocket:
                if not self.is_running:
                    logger.info("Receive loop stopped by flag")
                    break
                
                try:
                    # Parse message (JSON or plain text)
                    text = await self._parse_message(message)
                    
                    if text and self.on_text_received:
                        logger.info(f"📨 Received TTS request: '{text[:50]}{'...' if len(text) > 50 else ''}'")
                        await self.on_text_received(text)
                
                except Exception as e:
                    logger.error(f"Error processing message: {e}", exc_info=True)
        
        except websockets.exceptions.ConnectionClosed:
            logger.warning("WebSocket connection closed by server")
        except Exception as e:
            logger.error(f"Error in receive loop: {e}", exc_info=True)
        finally:
            self.is_connected = False
            self._disconnected_event.set()
            logger.info("📡 Stopped listening to WebSocket")
    
    async def _parse_message(self, message) -> Optional[str]:
        """
        Parse incoming message (JSON or plain text)
        
        Args:
            message: Raw message (str or bytes)
        
        Returns:
            Extracted text or None
        """
        try:
            # Convert bytes to string if needed
            if isinstance(message, bytes):
                message = message.decode('utf-8')
            
            # Try JSON parsing
            if message.startswith('{'):
                data = json.loads(message)
                return data.get("text", "").strip()
            else:
                # Plain text message
                return message.strip()
        
        except json.JSONDecodeError:
            # Not JSON, treat as plain text
            return message.strip()
        except Exception as e:
            logger.error(f"Failed to parse message: {e}")
            return None
    
    async def send_status(
        self,
        status: str,
        details: Optional[dict] = None,
        max_retries: int = 2
    ) -> bool:
        """
        Send status update to WebSocket server
        
        Args:
            status: Status type (e.g., "completed", "error", "processing")
            details: Additional status details
            max_retries: Retry attempts for failed sends
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected or not self.websocket:
            logger.warning("Cannot send status: WebSocket not connected")
            return False
        
        message = {
            "type": "tts_status",
            "session_id": self.session_id,
            "status": status,
            "timestamp": time.time()
        }
        
        if details:
            message["details"] = details
        
        for attempt in range(max_retries):
            try:
                await self.websocket.send(json.dumps(message))
                logger.debug(f"Status sent: {status}")
                return True
            except Exception as e:
                logger.warning(f"Failed to send status (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
        
        return False
    
    async def disconnect(self):
        """Disconnect from WebSocket and cleanup"""
        logger.info("Disconnecting TTS WebSocket...")
        
        self.is_running = False
        
        # Send final status
        if self.is_connected:
            await self.send_status("disconnecting")
        
        # Cancel receive task
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket connection
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception as e:
                logger.warning(f"Error closing WebSocket: {e}")
            finally:
                self.websocket = None
        
        self.is_connected = False
        self._disconnected_event.set()
        logger.info("✅ TTS WebSocket disconnected")
    
    async def wait_until_disconnected(self):
        """Wait until WebSocket is disconnected"""
        await self._disconnected_event.wait()
    
    def __del__(self):
        """Destructor - ensure cleanup"""
        if self.is_connected and self.websocket:
            # Schedule disconnect in event loop
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.disconnect())
            except:
                pass
