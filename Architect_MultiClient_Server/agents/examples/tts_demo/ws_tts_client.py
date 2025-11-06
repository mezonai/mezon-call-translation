"""
WebSocket TTS Client - Handles WebSocket communication for TTS requests
"""
import asyncio
import json
import time
from typing import Optional, Callable, Awaitable
import websockets


class WebSocketTTSClient:
    """WebSocket client for receiving TTS requests from server"""
    
    def __init__(
        self,
        session_id: str,
        ws_url: str,
        on_text_received: Optional[Callable[[str], Awaitable[None]]] = None,
        ping_interval: int = 20,
        ping_timeout: int = 10
    ):
        """
        Initialize WebSocket TTS Client
        
        Args:
            session_id: Unique session identifier
            ws_url: WebSocket server URL
            on_text_received: Async callback when text is received
            ping_interval: Ping interval in seconds
            ping_timeout: Ping timeout in seconds
        """
        self.session_id = session_id
        self.ws_url = ws_url
        self.on_text_received = on_text_received
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False
        self.is_running = False
        self.receive_task: Optional[asyncio.Task] = None
    
    async def connect(self) -> bool:
        """
        Connect to WebSocket server
        
        Returns:
            True if connected successfully, False otherwise
        """
        try:
            print(f"🔌 Connecting to WebSocket: {self.ws_url}")
            
            self.websocket = await websockets.connect(
                self.ws_url,
                ping_interval=self.ping_interval,
                ping_timeout=self.ping_timeout
            )
            
            self.connected = True
            self.is_running = True
            
            print(f"✅ WebSocket connected to {self.ws_url}")
            
            # Start receiving messages
            self.receive_task = asyncio.create_task(self._receive_loop())
            
            return True
            
        except Exception as e:
            print(f"❌ Failed to connect WebSocket: {e}")
            self.connected = False
            return False
    
    async def disconnect(self) -> None:
        """Disconnect from WebSocket server"""
        print("🔌 Disconnecting WebSocket...")
        
        self.is_running = False
        
        # Send disconnecting status
        await self.send_status("disconnecting")
        
        # Cancel receive task
        if self.receive_task and not self.receive_task.done():
            self.receive_task.cancel()
            try:
                await self.receive_task
            except asyncio.CancelledError:
                pass
        
        # Close WebSocket
        if self.websocket:
            try:
                await self.websocket.close()
            except Exception:
                pass
            self.websocket = None
        
        self.connected = False
        print("✅ WebSocket disconnected")
    
    async def _receive_loop(self) -> None:
        """Receive and process messages from WebSocket server"""
        print("📡 Listening for messages from WebSocket server...")
        
        try:
            async for message in self.websocket:
                if not self.is_running:
                    break
                
                try:
                    text = self._parse_message(message)
                    if text and self.on_text_received:
                        print(f"\n[{time.strftime('%H:%M:%S')}] 📨 Received text: {text[:50]}...")
                        await self.on_text_received(text)
                        
                except Exception as e:
                    print(f"❌ Error processing message: {e}")
                    import traceback
                    traceback.print_exc()
                    
        except websockets.exceptions.ConnectionClosed:
            print(f"⚠️ WebSocket connection closed")
        except Exception as e:
            print(f"❌ Error in receive loop: {e}")
            import traceback
            traceback.print_exc()
        finally:
            self.connected = False
            print("📡 Stopped listening to WebSocket")
    
    def _parse_message(self, message) -> Optional[str]:
        """
        Parse WebSocket message to extract text
        
        Args:
            message: Raw message from WebSocket
            
        Returns:
            Extracted text or None if no text found
        """
        try:
            # Try JSON format first
            if isinstance(message, str):
                if message.startswith('{'):
                    data = json.loads(message)
                    return data.get("text", "").strip()
                else:
                    return message.strip()
            else:
                # Binary message
                data = json.loads(message.decode('utf-8'))
                return data.get("text", "").strip()
                
        except json.JSONDecodeError:
            # Plain text message
            text = message if isinstance(message, str) else message.decode('utf-8')
            return text.strip()
        except Exception:
            return None
    
    async def send_status(
        self,
        status: str,
        details: Optional[dict] = None
    ) -> bool:
        """
        Send status update to WebSocket server
        
        Args:
            status: Status string (e.g., "completed", "error", "disconnecting")
            details: Optional additional details
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.connected or not self.websocket:
            return False
        
        try:
            message = {
                "type": "tts_status",
                "session_id": self.session_id,
                "status": status,
                "timestamp": time.time()
            }
            if details:
                message["details"] = details
            
            await self.websocket.send(json.dumps(message))
            return True
            
        except Exception as e:
            print(f"⚠️ Failed to send status: {e}")
            return False
    
    async def wait_until_disconnected(self) -> None:
        """Wait until WebSocket is disconnected"""
        while self.is_running and self.connected:
            await asyncio.sleep(1)
