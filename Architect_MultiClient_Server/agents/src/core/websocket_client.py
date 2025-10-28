import asyncio
import time
import websockets
from typing import Optional
from src.services.config_service import ConfigService
from src.services.metrics_service import MetricsService
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from src.logger import get_logger


logger = get_logger(__name__)


class WebSocketTranscriptionClient:
    """
    Optimized WebSocket client with batching and rate limiting
    """
    def __init__(self, client_id, session_id, transcription_callback=None, participant_identity=None):
        self.client_id = client_id
        self.session_id = session_id
        self.transcription_callback = transcription_callback
        self.participant_identity = participant_identity

        # Configuration
        self.config = ConfigService.get_instance().websocket
        
        # Metrics
        self.metrics = MetricsService.get_instance()
        
        # Circuit breaker
        self.circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=3,
            reset_timeout=30.0,
            half_open_timeout=5.0
        ))

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
        
        # Performance metrics
        self._track_metric("client.initialized", 1, {
            "client_id": client_id,
            "participant": participant_identity
        })

    async def connect(self):
        """Establish WebSocket connection to transcription server"""
        if self.reconnecting or not self.circuit_breaker.can_try():
            return False
            
        self.uri = (
            f"ws://{self.config.host}:{self.config.port}/ws/vosk/"
            f"?client_id={self.client_id}&session_id={self.session_id}"
        )
        
        connect_start = time.time()
        logger.info(f"Connecting to transcription server for {self.participant_identity}...")
        logger.debug(f"URI: {self.uri}")

        try:
            headers = {}

            self.websocket = await websockets.connect(
                self.uri,
                ping_interval=self.config.ping_interval,
                ping_timeout=self.config.ping_timeout,
                close_timeout=self.config.connection_timeout,
                max_size=None,
                max_queue=self.config.max_queue_size
            )
            self.connected = True
            self.connection_attempts = 0

            # Start receiving messages
            self.receive_task = asyncio.create_task(self._receive_messages())
            
            # Record success in circuit breaker
            self.circuit_breaker.record_success()
            
            # Track metrics
            connect_time = time.time() - connect_start
            self._track_metric("websocket.connect.time", connect_time)
            self._track_metric("websocket.connect.success", 1)

            logger.info(f"WebSocket connected for participant {self.participant_identity}")
            return True

        except Exception as e:
            logger.error(f"Failed to connect for {self.participant_identity}: {e}")
            self.connected = False
            
            # Record failure in circuit breaker
            if self.circuit_breaker.record_failure():
                logger.warning(f"Circuit breaker opened for {self.participant_identity}")
                
            # Track metrics
            self._track_metric("websocket.connect.failure", 1)
            return False
            
    def _track_metric(self, name: str, value: float, labels: dict = None):
        """Helper to track metrics with default labels"""
        default_labels = {
            "client_id": self.client_id,
            "participant": self.participant_identity
        }
        if labels:
            default_labels.update(labels)
        self.metrics.track(name, value, default_labels)

    async def reconnect(self, max_attempts: int = None, base_delay: float = None) -> bool:
        """Optimized reconnect with better error handling and circuit breaker"""
        if self.reconnecting or self.is_disconnecting:
            return False
            
        self.reconnecting = True
        max_attempts = max_attempts or self.config.reconnect_max_attempts
        base_delay = base_delay or self.config.reconnect_base_delay
        
        try:
            # Check circuit breaker
            if not self.circuit_breaker.can_try():
                logger.warning(f"Circuit breaker preventing reconnect for {self.participant_identity}")
                return False
            
            # Clean up existing connection first
            await self._cleanup_connection()
            reconnect_start = time.time()
            
            for attempt in range(1, max_attempts + 1):
                try:
                    logger.info(f"Reconnect attempt {attempt}/{max_attempts} for {self.participant_identity}")
                    
                    self.websocket = await websockets.connect(
                        self.uri,
                        ping_interval=self.config.ping_interval,
                        ping_timeout=self.config.ping_timeout,
                        close_timeout=self.config.connection_timeout,
                        max_size=None,
                        max_queue=self.config.max_queue_size,
                    )
                    self.connected = True
                    self.receive_task = asyncio.create_task(self._receive_messages())
                    
                    # Record success
                    self.circuit_breaker.record_success()
                    reconnect_time = time.time() - reconnect_start
                    self._track_metric("websocket.reconnect.time", reconnect_time)
                    self._track_metric("websocket.reconnect.success", 1)
                    
                    logger.info(f"Reconnected successfully for {self.participant_identity}")
                    return True
                    
                except Exception as e:
                    delay = min(base_delay * (2 ** (attempt - 1)), 30)  # Cap at 30 seconds
                    logger.warning(f"Reconnect failed for {self.participant_identity} (attempt {attempt}): {e}")
                    
                    # Record failure
                    if self.circuit_breaker.record_failure():
                        logger.warning(f"Circuit breaker opened during reconnect for {self.participant_identity}")
                        break
                    
                    if attempt < max_attempts:
                        logger.info(f"Retrying in {delay:.2f}s")
                        await asyncio.sleep(delay)
                    
            # Track failed reconnect
            self._track_metric("websocket.reconnect.failure", 1, {
                "attempts": attempt,
                "max_attempts": max_attempts
            })
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
            
        send_start = time.time()
        
        try:
            # Add to buffer for batching
            self.audio_buffer.append(audio_bytes)
            total_buffer_size = sum(len(chunk) for chunk in self.audio_buffer)
            
            # Track buffer metrics
            self._track_metric("audio.buffer.size", total_buffer_size)
            self._track_metric("audio.buffer.chunks", len(self.audio_buffer))
            
            # Check if we should send (based on buffer size or time)
            current_time = time.time()
            # should_send = (
            #     len(self.audio_buffer) >= self.config.batch_size or 
            #     total_buffer_size >= self.config.max_buffer_size or
            #     (current_time - self.last_send_time) > 0.1  # Force send every 100ms
            # )
            # print(len(self.audio_buffer) >= self.config.batch_size, total_buffer_size >= self.config.max_buffer_size, (current_time - self.last_send_time) > 0.1)
            # if not should_send:
            #     return
                
            # Rate limiting
            if current_time - self.last_send_time < self.config.send_delay:
                await asyncio.sleep(self.config.send_delay - (current_time - self.last_send_time))
            
            # Prepare batched data
            if not self.audio_buffer:
                return
                
            batched_data = b''.join(self.audio_buffer)
            batch_size = len(batched_data)
            ms_sent = int(batch_size / (16000*2) * 1000)
            self.audio_buffer.clear()
            
            # Check circuit breaker before sending
            if not self.circuit_breaker.can_try():
                logger.warning(f"Circuit breaker preventing audio send for {self.participant_identity}")
                return
            
            # Ensure connection
            if not self.connected or not self.websocket:
                if not await self.reconnect():
                    logger.error(f"Failed to reconnect for {self.participant_identity}, dropping audio data")
                    return
            
            # Send data with timeout
            if self.websocket and self.connected:
                try:
                    await asyncio.wait_for(
                        self.websocket.send(batched_data),
                        timeout=5.0
                    )
                    
                    # Record success
                    self.circuit_breaker.record_success()
                    self.last_send_time = time.time()
                    
                    # Track metrics
                    send_time = time.time() - send_start
                    self._track_metric("audio.send.time", send_time)
                    self._track_metric("audio.send.bytes", batch_size)
                    self._track_metric("audio.send.success", 1)
                    
                except asyncio.TimeoutError:
                    logger.error(f"Send timeout for {self.participant_identity}")
                    self.circuit_breaker.record_failure()
                    self._track_metric("audio.send.timeout", 1)
                    self.connected = False
                    
                except websockets.exceptions.ConnectionClosed:
                    logger.warning(f"Connection closed while sending for {self.participant_identity}")
                    self.circuit_breaker.record_failure()
                    self._track_metric("audio.send.connection_closed", 1)
                    self.connected = False
                    
                except Exception as e:
                    logger.error(f"Failed to send audio data for {self.participant_identity}: {e}")
                    self.circuit_breaker.record_failure()
                    self._track_metric("audio.send.error", 1)
                    self.connected = False
                    
        except Exception as e:
            logger.error(f"Error in send_audio for {self.participant_identity}: {e}")
            self._track_metric("audio.send.error", 1)

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