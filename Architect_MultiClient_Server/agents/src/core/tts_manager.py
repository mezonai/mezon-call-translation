"""
TTS Manager - Manages Text-to-Speech lifecycle and LiveKit audio integration
Coordinates TTS engine, DataChannel communication, and audio track management
"""
import asyncio
import json
import time
from typing import Optional
import numpy as np
from livekit import rtc, agents

from ..logger import get_logger
from ..services.tts_client import process_text_to_audio

logger = get_logger(__name__)


class TTSManager:
    """
    Manages TTS functionality: model loading, DataChannel communication,
    and audio streaming to LiveKit room
    """
    
    def __init__(
        self,
        ctx: agents.JobContext,
        session_id: str,
        sample_rate: int = 24000
    ):
        """
        Initialize TTS Manager
        
        Args:
            ctx: LiveKit job context
            session_id: Unique session identifier
            sample_rate: Audio sample rate in Hz (default: 24000 for Kokoro)
        """
        self.ctx = ctx
        self.session_id = session_id
        self.sample_rate = sample_rate
        
        # Audio track management (persistent track)
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        self.track_published = False
        
        # DataChannel handler registered flag
        self.handler_registered = False
        
        # Request queue management (prevent concurrent audio streaming)
        self._request_queue = asyncio.Queue()
        self._processing_task: Optional[asyncio.Task] = None
        self._is_processing = False
        
        # Statistics
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_audio_duration": 0.0,
            "total_synthesis_time": 0.0,
            "queued_requests": 0,
            "started_at": int(time.time() * 1000)
        }
        
        logger.info(
            f"TTSManager initialized (session={session_id}, "
            f"sample_rate={sample_rate}Hz, input=DataChannel)"
        )
    
    async def initialize(self) -> bool:
        """
        Initialize TTS system: load model and prepare for requests
        
        Note: Audio track setup is deferred until first TTS request (lazy initialization)
        This prevents microphone icon from appearing until TTS is actually used.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Initializing TTS Manager...")

            # Start request processing worker
            self._processing_task = asyncio.create_task(self._process_request_queue())
            logger.info("✅ TTS request queue worker started")
            
            logger.info("✅ TTS Manager initialized successfully (lazy track setup enabled)")
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to initialize TTS Manager: {e}", exc_info=True)
            return False
    
    async def _process_request_queue(self):
        """
        Background worker that processes TTS requests sequentially from queue
        This ensures only one audio stream is active at a time
        """
        logger.info("TTS request queue worker started")
        
        try:
            while True:
                # Wait for next request
                request_data = await self._request_queue.get()
                
                # Check for shutdown signal
                if request_data is None:
                    logger.info("TTS request queue worker shutting down")
                    break
                
                self._is_processing = True
                
                try:
                    text = request_data["text"]
                    sender_identity = request_data["sender_identity"]
                    voice = request_data.get("voice")
                    speed = request_data.get("speed")

                    logger.info(
                        f"📋 Processing queued TTS request "
                        f"(queue_size={self._request_queue.qsize()}): '{text[:30]}...'"
                    )
                    
                    # Process the request
                    await self._process_tts_request(text, sender_identity, voice=voice, speed=speed)
                    
                except Exception as e:
                    logger.error(f"Error processing queued TTS request: {e}", exc_info=True)
                
                finally:
                    self._is_processing = False
                    self._request_queue.task_done()
                    
        except asyncio.CancelledError:
            logger.info("TTS request queue worker cancelled")
        except Exception as e:
            logger.error(f"Fatal error in TTS request queue worker: {e}", exc_info=True)
    
    async def _setup_audio_track(self) -> bool:
        """
        Setup persistent audio track for TTS output (lazy initialization)
        
        Called automatically on first TTS request to avoid displaying microphone
        icon until TTS is actually needed.
        
        Returns:
            True if successful, False otherwise
        """
        try:
            # Create audio source
            self.audio_source = rtc.AudioSource(
                self.sample_rate,
                num_channels=1
            )
            logger.debug(f"Created AudioSource: sample_rate={self.sample_rate}, channels=1")
            
            # Create local audio track
            self.audio_track = rtc.LocalAudioTrack.create_audio_track(
                "tts-audio",
                self.audio_source
            )
            logger.debug("Created LocalAudioTrack from AudioSource")
            
            # Publish track to room
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_UNKNOWN  # Not a microphone, generic audio output
            
            publication = await self.ctx.room.local_participant.publish_track(
                self.audio_track,
                options
            )
            
            # Wait a moment for track to be fully ready
            await asyncio.sleep(0.1)
            
            self.track_published = True
            logger.info(f"✅ Published TTS audio track: {publication.sid}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to setup audio track: {e}", exc_info=True)
            self.track_published = False
            return False
    
    async def _send_tts_status(
        self,
        status: str,
        details: Optional[dict] = None,
        max_retries: int = 2
    ) -> bool:
        """
        Send TTS status update via DataChannel
        
        Args:
            status: Status type (e.g., "completed", "error", "processing")
            details: Additional status details (optional)
            max_retries: Retry attempts for failed sends
        
        Returns:
            True if successful, False otherwise
        """
        # Build status message
        message = {
            "type": "tts_status",
            "session_id": self.session_id,
            "status": status,
            "timestamp": int(time.time() * 1000)
        }
        
        if details:
            message["details"] = details
        
        # Send with retries
        for attempt in range(max_retries):
            try:
                await self.ctx.room.local_participant.publish_data(
                    json.dumps(message).encode("utf-8"),
                    reliable=True,
                    topic="tts_status"
                )
                
                logger.debug(f"TTS status sent: {status}")
                return True
                
            except Exception as e:
                logger.warning(
                    f"Failed to send TTS status (attempt {attempt + 1}/{max_retries}): {e}"
                )
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
        
        return False
    
    async def _process_tts_request(self, text: str, sender_identity: str = "unknown", voice: str = None, speed: float = None):
        """
        Process TTS request: synthesize text and stream audio to room
        
        Args:
            text: Text to synthesize
            sender_identity: Identity of the sender (for logging)
            voice: Voice to use (optional)
            speed: Speech speed (optional)
        """
        request_start = time.time()
        self.stats["total_requests"] += 1
        
        try:
            logger.info(
                f"🎤 Processing TTS request from {sender_identity}: "
                f"'{text[:50]}{'...' if len(text) > 50 else ''}'"
            )
            
            # Lazy initialization: Setup track on first use
            if not self.track_published or not self.audio_source:
                logger.info("🎬 First TTS request - setting up audio track...")
                if not await self._setup_audio_track():
                    raise RuntimeError("Failed to setup audio track for TTS")
                logger.info("✅ Audio track ready for TTS output")
            
            # Step 1: Synthesize text to audio
            logger.info("Step 1/2: Synthesizing audio...")
            synthesis_start = time.time()
            
            audio_data = await process_text_to_audio(text, voice=voice, speed=speed)
            
            synthesis_time = time.time() - synthesis_start
            self.stats["total_synthesis_time"] += synthesis_time
            
            logger.info(f"✅ Synthesis complete: {synthesis_time:.2f}s, audio_samples={len(audio_data)}")
            
            # Step 2: Stream audio to LiveKit room
            logger.info("Step 2/2: Streaming audio to room...")
            await self._publish_audio(audio_data)
            
            # Calculate stats
            total_time = time.time() - request_start
            audio_duration = len(audio_data) / self.sample_rate
            self.stats["total_audio_duration"] += audio_duration
            self.stats["successful_requests"] += 1
            
            logger.info(
                f"✅ TTS request completed "
                f"(synthesis={synthesis_time:.2f}s, "
                f"audio={audio_duration:.2f}s, "
                f"total={total_time:.2f}s)"
            )
            
            # Send completion status via DataChannel
            await self._send_tts_status("completed", {
                "text_length": len(text),
                "audio_duration": audio_duration,
                "synthesis_time": synthesis_time,
                "total_processing_time": total_time,
                "sender": sender_identity
            })
            
        except Exception as e:
            self.stats["failed_requests"] += 1
            logger.error(f"❌ Failed to process TTS request: {e}", exc_info=True)
            
            # Send error status via DataChannel
            await self._send_tts_status("error", {
                "error": str(e),
                "text_preview": text[:50],
                "sender": sender_identity
            })
    
    async def _publish_audio(self, audio_data: np.ndarray):
        """
        Publish audio to LiveKit room using AudioByteStream for proper chunking
        
        This method uses LiveKit's AudioByteStream to ensure:
        1. Fixed frame size (100ms chunks by default)
        2. Correct sample alignment
        3. Buffer management (only emit when enough data)
        
        Args:
            audio_data: Audio samples (float32, [-1.0, 1.0])
        
        Raises:
            RuntimeError: If audio track not ready
        """
        # Validate track state
        if not self.track_published:
            raise RuntimeError("Audio track not published")
        
        if not self.audio_source:
            raise RuntimeError("Audio source not initialized")
        
        if not self.audio_track:
            raise RuntimeError("Audio track not initialized")
        
        # Check if track is still valid (not unpublished)
        try:
            # Verify the track is still in the local participant's tracks
            local_tracks = self.ctx.room.local_participant.track_publications
            track_found = any(
                pub.track and pub.track.sid == self.audio_track.sid 
                for pub in local_tracks.values()
            )
            
            if not track_found:
                logger.error("Audio track no longer published in room")
                raise RuntimeError("Audio track was unpublished or disconnected")
                
        except Exception as e:
            logger.error(f"Failed to validate track state: {e}")
            raise RuntimeError(f"Invalid track state: {e}")
        
        try:
            from livekit.agents import utils
            
            # Convert float32 to PCM 16-bit (as bytes)
            # This is the standard format LiveKit expects
            audio_int16 = np.clip(audio_data * 32767, -32768, 32767).astype(np.int16)
            audio_bytes = audio_int16.tobytes()
            
            logger.debug(
                f"Publishing audio: {len(audio_int16)} samples "
                f"({len(audio_int16) / self.sample_rate:.2f}s), "
                f"{len(audio_bytes)} bytes"
            )
            
            # Create AudioByteStream - This handles proper chunking automatically
            # Default: 100ms chunks (sample_rate // 10)
            audio_bstream = utils.audio.AudioByteStream(
                sample_rate=self.sample_rate,
                num_channels=1,
                # samples_per_channel defaults to sample_rate // 10 (100ms)
                # For 24000 Hz: 2400 samples = 100ms chunks
            )
            
            # Push audio data through bytestream
            # This automatically creates properly sized AudioFrame objects
            frames = audio_bstream.push(audio_bytes)
            
            logger.debug(f"AudioByteStream generated {len(frames)} frames")
            
            # Stream all frames to LiveKit
            frame_count = 0
            for frame in frames:
                try:
                    await self.audio_source.capture_frame(frame)
                    frame_count += 1
                except Exception as e:
                    logger.error(f"Failed to capture frame {frame_count + 1}/{len(frames)}: {e}")
                    raise
            
            # Flush any remaining data in buffer
            remaining_frames = audio_bstream.flush()
            if remaining_frames:
                logger.debug(f"Flushing {len(remaining_frames)} remaining frames")
                for frame in remaining_frames:
                    try:
                        await self.audio_source.capture_frame(frame)
                        frame_count += 1
                    except Exception as e:
                        logger.error(f"Failed to capture flush frame: {e}")
                        raise
            
            logger.debug(f"Audio streaming completed successfully: {frame_count} frames sent")
            
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
            "current_queue_size": self._request_queue.qsize(),
            "is_processing": self._is_processing,
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
    
    
    async def cleanup(self):
        """Cleanup TTS resources and connections"""
        try:
            logger.info("Cleaning up TTS Manager...")
            
            # Stop request queue worker
            if self._processing_task and not self._processing_task.done():
                logger.info("Stopping TTS request queue worker...")
                # Send shutdown signal
                await self._request_queue.put(None)
                
                try:
                    # Wait for worker to finish with timeout
                    await asyncio.wait_for(self._processing_task, timeout=5.0)
                    logger.info("✅ TTS request queue worker stopped")
                except asyncio.TimeoutError:
                    logger.warning("TTS request queue worker did not stop in time, cancelling...")
                    self._processing_task.cancel()
                    try:
                        await self._processing_task
                    except asyncio.CancelledError:
                        pass
            
            # Flush audio buffer with silent frame using AudioByteStream
            if self.audio_source:
                try:
                    from livekit.agents import utils
                    
                    # Create 100ms of silence (standard chunk size)
                    samples = self.sample_rate // 10  # 100ms
                    silent_audio = np.zeros(samples, dtype=np.int16).tobytes()
                    
                    audio_bstream = utils.audio.AudioByteStream(
                        sample_rate=self.sample_rate,
                        num_channels=1,
                    )
                    
                    frames = audio_bstream.push(silent_audio)
                    for frame in frames:
                        await self.audio_source.capture_frame(frame)
                    
                except Exception as e:
                    logger.warning(f"Failed to flush audio buffer: {e}")

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
