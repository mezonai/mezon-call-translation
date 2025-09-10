import asyncio
import time
import websockets

from src.config import (
    WEBSOCKET_HOST, WEBSOCKET_PORT, BATCH_SIZE, SEND_DELAY,
    MAX_BUFFER_SIZE, RECONNECT_MAX_ATTEMPTS, RECONNECT_BASE_DELAY
)
from src.logger import get_logger


logger = get_logger(__name__)


class WebSocketTranscriptionClient:
    """
    Optimized WebSocket client with batching and rate limiting
    """
    def __init__(self, client_id, session_id, transcript=True, translation=True,
                 transcription_callback=None, participant_identity=None):
        self.client_id = client_id
        self.session_id = session_id
        self.transcript = transcript
        self.translation = translation
        self.transcription_callback = transcription_callback
        self.participant_identity = participant_identity

        self.websocket = None
        self.receive_task = None
        self.connected = False
        self.uri = None
        self.reconnecting = False
        
        # Optimization additions
        self.audio_buffer = []
        self.last_send_time = 0
        self.connection_attempts = 0
        self.is_disconnecting = False

    async def connect(self):
        """Establish WebSocket connection to transcription server"""
        if self.reconnecting:
            return False
            
        self.uri = (
            f"ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}/ws/vosk/"
            f"?client_id={self.client_id}&session_id={self.session_id}"
            f"&transcript={str(self.transcript).lower()}&translation={str(self.translation).lower()}"
        )
        
        logger.info(f"Connecting to transcription server for {self.participant_identity}...")
        logger.debug(f"URI: {self.uri}")

        try:
            self.websocket = await websockets.connect(
                self.uri,
                ping_interval=30,  # Increased ping interval
                ping_timeout=15,   # Increased timeout
                close_timeout=10,
                max_size=None,
                max_queue=32,      # Limit queue size
            )
            self.connected = True
            self.connection_attempts = 0

            # Start receiving messages
            self.receive_task = asyncio.create_task(self._receive_messages())

            logger.info(f"WebSocket connected for participant {self.participant_identity}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect for {self.participant_identity}: {e}")
            self.connected = False
            return False

    async def reconnect(self, max_attempts: int = None, base_delay: float = None) -> bool:
        """Optimized reconnect with better error handling"""
        if self.reconnecting or self.is_disconnecting:
            return False
            
        self.reconnecting = True
        max_attempts = max_attempts or RECONNECT_MAX_ATTEMPTS
        base_delay = base_delay or RECONNECT_BASE_DELAY
        
        try:
            # Clean up existing connection first
            await self._cleanup_connection()
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Reconnect attempt {attempt}/{max_attempts} for {self.participant_identity}")
                    
                    self.websocket = await websockets.connect(
                        self.uri,
                        ping_interval=30,
                        ping_timeout=15,
                        close_timeout=10,
                        max_size=None,
                        max_queue=32,
                    )
                    self.connected = True
                    self.receive_task = asyncio.create_task(self._receive_messages())
                    
                    logger.info(f"Reconnected successfully for {self.participant_identity}")
                    return True
                    
                except Exception as e:
                    delay = min(base_delay * (2 ** (attempt - 1)), 30)  # Cap at 30 seconds
                    logger.warning(f"Reconnect failed for {self.participant_identity} (attempt {attempt}): {e}")
                    
                    if attempt < max_attempts:
                        logger.info(f"Retrying in {delay:.2f}s")
                        await asyncio.sleep(delay)
                    
            logger.error(f"All reconnect attempts failed for {self.participant_identity}")
            return False
            
        finally:
            self.reconnecting = False

    async def _cleanup_connection(self):
        """Clean up existing connection resources"""
        try:
            if self.receive_task and not self.receive_task.done():
                self.receive_task.cancel()
                try:
                    await self.receive_task
                except asyncio.CancelledError:
                    pass
                    
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
                
        except Exception as e:
            logger.debug(f"Error during cleanup for {self.participant_identity}: {e}")
        finally:
            self.websocket = None
            self.receive_task = None
            self.connected = False

    async def _receive_messages(self):
        """Receive and process messages from transcription server"""
        try:
            async for message in self.websocket:
                # Only debug log if message is not JSON or doesn't have text
                if not message.startswith('{'):
                    logger.debug(f"Received raw message from {self.participant_identity}")
                
                if self.transcription_callback:
                    try:
                        await self.transcription_callback(message)
                    except Exception as e:
                        logger.error(f"Transcription callback error for {self.participant_identity}: {e}")
                        
        except websockets.exceptions.ConnectionClosed as e:
            if not self.is_disconnecting:
                logger.warning(f"WebSocket closed for {self.participant_identity}: {e}")
        except Exception as e:
            logger.error(f"Error receiving messages for {self.participant_identity}: {e}")
        finally:
            self.connected = False

    async def send_audio(self, audio_bytes: bytes):
        """Send raw PCM16 bytes with batching and rate limiting"""
        if self.is_disconnecting:
            return
            
        # Add to buffer for batching
        self.audio_buffer.append(audio_bytes)
        
        # Check if we should send (based on buffer size or time)
        current_time = time.time()
        should_send = (
            len(self.audio_buffer) >= BATCH_SIZE or 
            sum(len(chunk) for chunk in self.audio_buffer) >= MAX_BUFFER_SIZE or
            (current_time - self.last_send_time) > 0.1  # Force send every 100ms
        )
        
        if not should_send:
            return
            
        # Rate limiting
        if current_time - self.last_send_time < SEND_DELAY:
            await asyncio.sleep(SEND_DELAY - (current_time - self.last_send_time))
        
        # Prepare batched data
        if not self.audio_buffer:
            return
            
        batched_data = b''.join(self.audio_buffer)
        self.audio_buffer.clear()
        
        # Ensure connection
        if not self.connected or not self.websocket:
            if not await self.reconnect():
                logger.error(f"Failed to reconnect for {self.participant_identity}, dropping audio data")
                return
        
        # Send data
        if self.websocket and self.connected:
            try:
                await self.websocket.send(batched_data)
                self.last_send_time = time.time()
            except websockets.exceptions.ConnectionClosed:
                logger.warning(f"Connection closed while sending for {self.participant_identity}")
                self.connected = False
                
            except Exception as e:
                logger.error(f"Failed to send audio data for {self.participant_identity}: {e}")
                self.connected = False

    async def flush_buffer(self):
        """Force send any remaining buffered audio data"""
        if self.audio_buffer and self.connected and self.websocket:
            try:
                batched_data = b''.join(self.audio_buffer)
                self.audio_buffer.clear()
                await self.websocket.send(batched_data)
                logger.debug(f"Flushed {len(batched_data)} bytes for {self.participant_identity}")
            except Exception as e:
                logger.error(f"Error flushing buffer for {self.participant_identity}: {e}")

    async def disconnect(self):
        """Clean shutdown of WebSocket connection"""
        if self.is_disconnecting:
            return
            
        self.is_disconnecting = True
        logger.info(f"Disconnecting {self.participant_identity}")
        
        try:
            # Flush any remaining audio data
            await self.flush_buffer()
            
            # Clean up connection
            await self._cleanup_connection()
            
        except Exception as e:
            logger.error(f"Error during disconnect for {self.participant_identity}: {e}")
        finally:
            self.audio_buffer.clear()
            logger.info(f"Disconnected {self.participant_identity}")