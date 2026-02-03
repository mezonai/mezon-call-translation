"""
Speech-to-Text WebSocket Client - Vosk transcription service

This client handles audio streaming to Vosk transcription server
with optimized batching and rate limiting for real-time performance.
"""
import asyncio
import time
from typing import Optional, Callable, Awaitable

from src.core.websocket.base_client import BaseWebSocketClient
from src.config import get_config
from src.logger import get_logger


logger = get_logger(__name__)


class STTWebSocketClient(BaseWebSocketClient):
    """
    Speech-to-Text WebSocket client with audio batching
    
    Optimized for real-time audio streaming:
    - Audio buffer batching for efficiency
    - Rate limiting to prevent server overload
    - Automatic flush on disconnect
    - Per-participant tracking
    """
    
    def __init__(
        self,
        client_id: str,
        session_id: str,
        transcription_callback: Optional[Callable] = None,
        participant_identity: Optional[str] = None
    ):
        """
        Initialize STT WebSocket client
        
        Args:
            client_id: Unique client identifier
            session_id: Session ID for transcription
            transcription_callback: Async callback for transcription results
            participant_identity: Participant identity for logging
        """
        # Configuration
        self.config = get_config().stt_service
        
        # Build WebSocket URL
        url = (
            f"ws://{self.config.host}:{self.config.port}/ws/vosk/"
            f"?client_id={client_id}&session_id={session_id}"
        )
        
        # Initialize base class
        super().__init__(
            url=url,
            client_id=client_id,
            max_retries=self.config.reconnect_max_attempts,
            retry_delay=self.config.reconnect_base_delay,
            ping_interval=self.config.ping_interval,
            ping_timeout=self.config.ping_timeout
        )
        
        # STT-specific attributes
        self.session_id = session_id
        self.transcription_callback = transcription_callback
        self.participant_identity = participant_identity or client_id
        
        # Audio buffering
        self.audio_buffer = []
        self.last_send_time = 0
        
        # Update logger with participant context
        self.logger = get_logger(f"stt.{self.participant_identity}")
        
        self.logger.info(
            f"STT client initialized for {self.participant_identity} "
            f"(session={session_id})"
        )
    
    async def on_message(self, message):
        """
        Handle transcription results from server
        
        Non-blocking: callback runs in background to prevent blocking
        the WebSocket receive loop and causing audio glitches.
        
        Args:
            message: Transcription result (JSON or text)
        """
        # Only debug log if message is not JSON
        if not isinstance(message, str) or not message.startswith('{'):
            self.logger.debug(f"Received raw transcription result")
        
        # Run callback in background to avoid blocking receive loop
        if self.transcription_callback:
            asyncio.create_task(self._handle_transcription_callback(message))
    
    async def _handle_transcription_callback(self, message):
        """
        Handle transcription callback in background task
        
        Args:
            message: Transcription result to process
        """
        try:
            await self.transcription_callback(message)
        except Exception as e:
            self.logger.error(
                f"Transcription callback error: {e}",
                exc_info=True
            )
            self._track_metric("callback.error", 1)
    
    async def send_audio(self, audio_bytes: bytes):
        """
        Send audio data with batching and rate limiting
        
        This method batches audio chunks for efficiency and applies
        rate limiting to prevent server overload.
        
        Args:
            audio_bytes: Raw PCM16 audio data (16kHz, 16-bit, mono)
        """
        if not self.is_running:
            return
        
        send_start = time.time()
        
        try:
            # Add to buffer for batching
            self.audio_buffer.append(audio_bytes)
            total_buffer_size = sum(len(chunk) for chunk in self.audio_buffer)
            
            # Track buffer metrics
            self._track_metric("audio.buffer.size", total_buffer_size)
            self._track_metric("audio.buffer.chunks", len(self.audio_buffer))
            
            # Rate limiting - wait if sending too fast
            current_time = time.time()
            if current_time - self.last_send_time < self.config.send_delay:
                time_to_wait = self.config.send_delay - (current_time - self.last_send_time)
                await asyncio.sleep(time_to_wait)
            
            # Check if buffer is empty (shouldn't happen, but defensive)
            if not self.audio_buffer:
                return
            
            # Prepare batched data
            batched_data = b''.join(self.audio_buffer)
            batch_size = len(batched_data)
            self.audio_buffer.clear()
            
            # Check circuit breaker
            if not self.circuit_breaker.can_try():
                self.logger.warning("Circuit breaker preventing audio send")
                self._track_metric("audio.circuit_breaker_open", 1)
                return
            
            # Ensure connection
            if not self.is_connected or not self.websocket:
                if not await self.reconnect():
                    self.logger.error("Failed to reconnect, dropping audio data")
                    self._track_metric("audio.dropped", batch_size)
                    return
            
            # Send data with timeout
            if self.websocket and self.is_connected:
                try:
                    await asyncio.wait_for(
                        self.websocket.send(batched_data),
                        timeout=5.0
                    )
                    
                    # Update timing
                    self.last_send_time = time.time()
                    
                    # Track metrics
                    send_time = time.time() - send_start
                    self._track_metric("audio.send.time", send_time)
                    self._track_metric("audio.send.bytes", batch_size)
                    
                    # Calculate audio duration sent (PCM16 at 16kHz)
                    audio_duration_ms = int(batch_size / (16000 * 2) * 1000)
                    self._track_metric("audio.duration_ms", audio_duration_ms)
                    
                except asyncio.TimeoutError:
                    self.logger.error("Audio send timeout")
                    self.circuit_breaker.record_failure()
                    self._track_metric("audio.timeout", 1)
                    self.is_connected = False
                    
                except Exception as e:
                    self.logger.error(f"Failed to send audio: {e}")
                    self.circuit_breaker.record_failure()
                    self._track_metric("audio.error", 1)
                    self.is_connected = False
        
        except Exception as e:
            self.logger.error(f"Error in send_audio: {e}", exc_info=True)
            self._track_metric("audio.processing_error", 1)
    
    async def flush_buffer(self):
        """
        Force send any remaining buffered audio data
        
        Called during graceful shutdown to ensure no audio is lost.
        """
        if self.audio_buffer and self.is_connected and self.websocket:
            try:
                batched_data = b''.join(self.audio_buffer)
                batch_size = len(batched_data)
                self.audio_buffer.clear()
                
                await self.websocket.send(batched_data)
                
                self.logger.debug(f"Flushed {batch_size} bytes")
                self._track_metric("audio.flushed", batch_size)
                
            except Exception as e:
                self.logger.error(f"Error flushing buffer: {e}")
                self._track_metric("audio.flush_error", 1)
    
    async def disconnect(self):
        """
        Gracefully disconnect with audio buffer flush
        
        Ensures all buffered audio is sent before closing connection.
        """
        self.logger.info(f"Disconnecting STT client for {self.participant_identity}")
        
        try:
            # Flush any remaining audio
            await self.flush_buffer()
        except Exception as e:
            self.logger.error(f"Error during flush: {e}")
        finally:
            # Clear buffer
            self.audio_buffer.clear()
            
            # Call base class disconnect
            await super().disconnect()
            
            self.logger.info(f"✅ STT client disconnected for {self.participant_identity}")
    
    def get_stats(self) -> dict:
        """
        Get STT client statistics
        
        Returns:
            Dictionary with connection and audio stats
        """
        stats = super().get_stats()
        
        # Add STT-specific stats
        stats.update({
            "session_id": self.session_id,
            "participant_identity": self.participant_identity,
            "buffered_chunks": len(self.audio_buffer),
            "buffered_bytes": sum(len(chunk) for chunk in self.audio_buffer)
        })
        
        return stats
