"""
Text-to-Speech WebSocket Client - TTS service communication

This client handles text streaming to TTS server and status updates
with automatic reconnection and message parsing.
"""
import asyncio
import json
import time
from typing import Optional, Callable, Awaitable

from src.core.websocket.base_client import BaseWebSocketClient
from src.logger import get_logger


logger = get_logger(__name__)


class TTSWebSocketClient(BaseWebSocketClient):
    """
    Text-to-Speech WebSocket client
    
    Features:
    - Async text message reception
    - JSON and plain text parsing
    - Status update sending
    - Session-based routing
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
        Initialize TTS WebSocket client
        
        Args:
            session_id: Unique session identifier
            ws_url: WebSocket server URL (default: ws://localhost:8089/ws/tts/{session_id})
            on_text_received: Async callback when text is received
            max_retries: Maximum connection retry attempts
            retry_delay: Base delay between retries (seconds)
        """
        self.session_id = session_id
        self.on_text_received = on_text_received
        
        # Build WebSocket URL
        url = ws_url or f"ws://localhost:8089/ws/tts/{session_id}"
        
        # Initialize base class
        super().__init__(
            url=url,
            client_id=f"tts_{session_id}",
            max_retries=max_retries,
            retry_delay=retry_delay,
            ping_interval=20.0,
            ping_timeout=10.0
        )
        
        # Update logger with TTS context
        self.logger = get_logger(f"tts.{session_id}")
        
        self.logger.info(f"TTS client initialized (session={session_id}, url={url})")
    
    async def connect(self) -> bool:
        """
        Connect to TTS WebSocket server with troubleshooting
        
        Returns:
            True if successful, False otherwise
        """
        success = await super().connect()
        
        if success:
            self.logger.info("📡 Listening for TTS messages from server...")
        else:
            self._print_troubleshooting_guide()
        
        return success
    
    def _print_troubleshooting_guide(self):
        """Print troubleshooting information when connection fails"""
        self.logger.error("=" * 60)
        self.logger.error("TTS WebSocket Connection Failed - Troubleshooting:")
        self.logger.error(f"  1. Verify server is running:")
        self.logger.error(f"     python test_tts_websocket_server.py 8089")
        self.logger.error(f"  2. Check URL: {self.url}")
        self.logger.error(f"  3. Set custom URL via environment:")
        self.logger.error(f"     TTS_WS_URL='ws://localhost:PORT/ws/tts/{{session_id}}'")
        self.logger.error(f"  4. Check firewall/network settings")
        self.logger.error("=" * 60)
    
    async def on_message(self, message):
        """
        Handle text messages from TTS server
        
        Parses JSON or plain text messages and dispatches to callback.
        Non-blocking: callback runs in background to prevent audio glitches.
        
        Args:
            message: Message from server (str or bytes)
        """
        try:
            # Parse message (JSON or plain text)
            text = await self._parse_message(message)
            
            if text and self.on_text_received:
                # Truncate for logging
                display_text = text[:50] + ('...' if len(text) > 50 else '')
                self.logger.info(f"📨 Received TTS request: '{display_text}'")
                
                # Run callback in background to avoid blocking receive loop
                # This prevents audio glitches when TTS synthesis takes time
                asyncio.create_task(self._handle_text_callback(text))
        
        except Exception as e:
            self.logger.error(f"Error processing TTS message: {e}", exc_info=True)
            self._track_metric("message.processing_error", 1)
    
    async def _handle_text_callback(self, text: str):
        """
        Handle text callback in background task
        
        Args:
            text: Text to process for TTS
        """
        try:
            await self.on_text_received(text)
        except Exception as e:
            self.logger.error(f"Error in TTS callback: {e}", exc_info=True)
            self._track_metric("callback.error", 1)
    
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
                text = data.get("text", "").strip()
                
                # Log additional JSON fields if present
                if "language" in data:
                    self.logger.debug(f"TTS language: {data['language']}")
                if "voice" in data:
                    self.logger.debug(f"TTS voice: {data['voice']}")
                
                return text
            else:
                # Plain text message
                return message.strip()
        
        except json.JSONDecodeError:
            # Not JSON, treat as plain text
            return message.strip()
        except Exception as e:
            self.logger.error(f"Failed to parse message: {e}")
            self._track_metric("message.parse_error", 1)
            return None
    
    async def send_status(
        self,
        status: str,
        details: Optional[dict] = None,
        max_retries: int = 2
    ) -> bool:
        """
        Send status update to TTS server
        
        Args:
            status: Status type (e.g., "completed", "error", "processing")
            details: Additional status details (optional)
            max_retries: Retry attempts for failed sends
        
        Returns:
            True if successful, False otherwise
        """
        if not self.is_connected or not self.websocket:
            self.logger.warning("Cannot send status: WebSocket not connected")
            return False
        
        # Build status message
        message = {
            "type": "tts_status",
            "session_id": self.session_id,
            "status": status,
            "timestamp": time.time()
        }
        
        if details:
            message["details"] = details
        
        # Send with retries
        for attempt in range(max_retries):
            try:
                message_json = json.dumps(message)
                await self.websocket.send(message_json)
                
                self.logger.debug(f"Status sent: {status}")
                self._track_metric("status.sent", 1)
                
                return True
                
            except Exception as e:
                self.logger.warning(
                    f"Failed to send status (attempt {attempt + 1}/{max_retries}): {e}"
                )
                self._track_metric("status.send_error", 1)
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
        
        return False
    
    async def disconnect(self):
        """
        Gracefully disconnect with status notification
        
        Sends disconnecting status before closing connection.
        """
        self.logger.info("Disconnecting TTS client...")
        
        # Send final status if connected
        if self.is_connected:
            try:
                await self.send_status("disconnecting")
            except Exception as e:
                self.logger.debug(f"Could not send disconnect status: {e}")
        
        # Call base class disconnect
        await super().disconnect()
        
        self.logger.info("✅ TTS client disconnected")
    
    def get_stats(self) -> dict:
        """
        Get TTS client statistics
        
        Returns:
            Dictionary with connection and message stats
        """
        stats = super().get_stats()
        
        # Add TTS-specific stats
        stats.update({
            "session_id": self.session_id
        })
        
        return stats
