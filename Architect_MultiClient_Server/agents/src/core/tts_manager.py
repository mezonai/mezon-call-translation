"""
TTS Manager - Manages Text-to-Speech lifecycle and LiveKit audio integration
Coordinates TTS engine, WebSocket client, and audio track management
"""
import asyncio
import json
import time
from typing import Optional
import numpy as np
from livekit import rtc, agents

from ..logger import get_logger
from ..services.tts_engine import TTSEngine
from ..services.tts_websocket_client import TTSWebSocketClient

logger = get_logger(__name__)


class TTSManager:
    """
    Manages TTS functionality: model loading, WebSocket communication,
    and audio streaming to LiveKit room
    """
    
    def __init__(
        self,
        ctx: agents.JobContext,
        session_id: str,
        ws_url: Optional[str] = None,
        sample_rate: int = 48000,
        model_path: Optional[str] = None
    ):
        """
        Initialize TTS Manager
        
        Args:
            ctx: LiveKit job context
            session_id: Unique session identifier
            ws_url: WebSocket server URL (optional, uses default if not provided)
            sample_rate: Audio sample rate in Hz
            model_path: Path to TTS model (optional)
        """
        self.ctx = ctx
        self.session_id = session_id
        self.sample_rate = sample_rate
        
        # Initialize TTS engine
        self.tts_engine = TTSEngine(
            sample_rate=sample_rate,
            model_path=model_path
        )
        
        # Initialize WebSocket client
        self.ws_client = TTSWebSocketClient(
            session_id=session_id,
            ws_url=ws_url,
            on_text_received=self._on_text_received
        )
        
        # Audio track management (persistent track)
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        self.track_published = False
        
        # Statistics
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_audio_duration": 0.0,
            "total_synthesis_time": 0.0,
            "started_at": int(time.time() * 1000)
        }
        
        logger.info(
            f"TTSManager initialized (session={session_id}, "
            f"sample_rate={sample_rate}Hz)"
        )
    
    async def initialize(self) -> bool:
        """
        Initialize TTS system: load model, setup audio track, connect WebSocket
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Initializing TTS Manager...")
            
            # Step 1: Load TTS model
            logger.info("Step 1/3: Loading TTS model...")
            if not await self.tts_engine.load():
                logger.error("Failed to load TTS model")
                return False
            
            # Step 2: Setup audio track
            logger.info("Step 2/3: Setting up audio track...")
            if not await self._setup_audio_track():
                logger.error("Failed to setup audio track")
                return False
            
            # Step 3: Connect to WebSocket server
            logger.info("Step 3/3: Connecting to WebSocket server...")
            if not await self.ws_client.connect():
                logger.error("Failed to connect to WebSocket server")
                return False
            
            logger.info("✅ TTS Manager initialized successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize TTS Manager: {e}", exc_info=True)
            return False
    
    async def _setup_audio_track(self) -> bool:
        """
        Setup persistent audio track for TTS output
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create audio source
            self.audio_source = rtc.AudioSource(
                self.sample_rate,
                num_channels=1
            )
            
            # Create local audio track
            self.audio_track = rtc.LocalAudioTrack.create_audio_track(
                "tts-audio",
                self.audio_source
            )
            
            # Publish track to room
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_MICROPHONE
            
            publication = await self.ctx.room.local_participant.publish_track(
                self.audio_track,
                options
            )
            
            self.track_published = True
            logger.info(f"✅ Published TTS audio track: {publication.sid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup audio track: {e}", exc_info=True)
            self.track_published = False
            return False
    
    async def _on_text_received(self, text: str):
        """
        Callback when text is received from WebSocket server
        
        Args:
            text: Text to synthesize and play
        """
        await self._process_tts_request(text)
    
    async def _process_tts_request(self, text: str):
        """
        Process TTS request: synthesize text and stream audio to room
        
        Args:
            text: Text to synthesize
        """
        request_start = time.time()
        self.stats["total_requests"] += 1
        
        try:
            logger.info(f"Processing TTS request: '{text[:50]}{'...' if len(text) > 50 else ''}'")
            
            # Step 1: Synthesize text to audio
            logger.debug("Step 1/3: Synthesizing audio...")
            synthesis_start = time.time()
            
            audio_data = self.tts_engine.synthesize(text)
            
            synthesis_time = time.time() - synthesis_start
            self.stats["total_synthesis_time"] += synthesis_time
            
            # Step 2: Stream audio to LiveKit room
            logger.debug("Step 2/3: Streaming audio to room...")
            await self._publish_audio(audio_data)
            
            # Step 3: Calculate stats and send status
            total_time = time.time() - request_start
            audio_duration = self.tts_engine.get_audio_duration(audio_data)
            self.stats["total_audio_duration"] += audio_duration
            self.stats["successful_requests"] += 1
            
            logger.info(
                f"✅ TTS request completed "
                f"(synthesis={synthesis_time:.2f}s, "
                f"audio={audio_duration:.2f}s, "
                f"total={total_time:.2f}s)"
            )
            
            # Send completion status to server
            logger.debug("Step 3/3: Sending status to server...")
            await self.ws_client.send_status("completed", {
                "text_length": len(text),
                "audio_duration": audio_duration,
                "synthesis_time": synthesis_time,
                "total_processing_time": total_time
            })
            
        except Exception as e:
            self.stats["failed_requests"] += 1
            logger.error(f"Failed to process TTS request: {e}", exc_info=True)
            
            # Send error status
            await self.ws_client.send_status("error", {
                "error": str(e),
                "text_preview": text[:50]
            })
    
    async def _publish_audio(self, audio_data: np.ndarray):
        """
        Publish audio to LiveKit room
        
        Args:
            audio_data: Audio samples (float32, [-1.0, 1.0])
        
        Raises:
            RuntimeError: If audio track not ready
        """
        if not self.track_published or not self.audio_source:
            raise RuntimeError("Audio track not ready for publishing")
        
        try:
            # Convert float32 to int16 for LiveKit
            audio_int16 = (audio_data * 32767).astype(np.int16)
            
            # Stream audio in 10ms chunks for smooth playback
            chunk_size = self.sample_rate // 100  # 10ms chunks
            total_chunks = len(audio_int16) // chunk_size
            
            logger.debug(
                f"Streaming {total_chunks} chunks "
                f"({len(audio_int16) / self.sample_rate:.2f}s)"
            )
            
            for i in range(0, len(audio_int16), chunk_size):
                # Check if still running
                if not self.ws_client.is_running:
                    logger.warning("Audio streaming interrupted")
                    break
                
                chunk = audio_int16[i:i + chunk_size]
                
                # Pad last chunk if needed
                if len(chunk) < chunk_size:
                    chunk = np.pad(
                        chunk,
                        (0, chunk_size - len(chunk)),
                        mode='constant'
                    )
                
                # Create and send audio frame
                frame = rtc.AudioFrame(
                    data=chunk.tobytes(),
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    samples_per_channel=len(chunk)
                )
                
                await self.audio_source.capture_frame(frame)
            
            # Ensure last frames are flushed
            await asyncio.sleep(0.1)
            logger.debug("Audio streaming completed")
            
        except Exception as e:
            logger.error(f"Failed to publish audio: {e}", exc_info=True)
            raise
    
    def get_stats(self) -> dict:
        """
        Get TTS statistics
        
        Returns:
            Dictionary with TTS stats
        """
        uptime = (time.time() * 1000) - self.stats["started_at"]
        
        return {
            **self.stats,
            "uptime_ms": int(uptime),
            "success_rate": (
                self.stats["successful_requests"] / self.stats["total_requests"]
                if self.stats["total_requests"] > 0 else 0.0
            ),
            "avg_synthesis_time": (
                self.stats["total_synthesis_time"] / self.stats["successful_requests"]
                if self.stats["successful_requests"] > 0 else 0.0
            )
        }
    
    async def announce_tts_ready(self, max_retries: int = 3) -> bool:
        """
        Announce TTS service is ready via data channel
        
        Args:
            max_retries: Maximum retry attempts
        
        Returns:
            True if successful, False otherwise
        """
        for attempt in range(max_retries):
            try:
                announcement = {
                    "type": "tts_announcement",
                    "event": "tts_ready",
                    "tts": {
                        "session_id": self.session_id,
                        "sample_rate": self.sample_rate,
                        "status": "ready",
                        "capabilities": [
                            "text_to_speech",
                            "real_time_synthesis",
                            "websocket_control"
                        ]
                    },
                    "timestamp": int(time.time() * 1000)
                }
                
                await asyncio.wait_for(
                    self.ctx.room.local_participant.publish_data(
                        json.dumps(announcement).encode("utf-8"),
                        reliable=True,
                        topic="tts_control"
                    ),
                    timeout=5.0
                )
                
                logger.info("TTS ready announcement sent via data channel")
                return True
                
            except Exception as e:
                logger.warning(
                    f"Failed to announce TTS ready (attempt {attempt + 1}/{max_retries}): {e}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.0 * (attempt + 1))
        
        return False
    
    async def cleanup(self):
        """Cleanup TTS resources and connections"""
        try:
            logger.info("Cleaning up TTS Manager...")
            
            # Disconnect WebSocket
            await self.ws_client.disconnect()
            
            # Flush audio buffer with silent frame
            if self.audio_source:
                try:
                    silent_frame = rtc.AudioFrame(
                        data=np.zeros(480, dtype=np.int16).tobytes(),
                        sample_rate=self.sample_rate,
                        num_channels=1,
                        samples_per_channel=480
                    )
                    await self.audio_source.capture_frame(silent_frame)
                except Exception as e:
                    logger.warning(f"Failed to flush audio buffer: {e}")
            
            # Cleanup TTS engine
            self.tts_engine.cleanup()
            
            # Log final stats
            stats = self.get_stats()
            logger.info(
                f"TTS Manager cleanup completed - Stats: "
                f"requests={stats['total_requests']}, "
                f"success_rate={stats['success_rate']:.1%}, "
                f"total_audio={stats['total_audio_duration']:.1f}s"
            )
            
        except Exception as e:
            logger.error(f"Error during TTS cleanup: {e}", exc_info=True)
