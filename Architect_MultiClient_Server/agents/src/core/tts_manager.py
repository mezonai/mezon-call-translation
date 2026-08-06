"""
TTS Manager - Manages Text-to-Speech lifecycle and LiveKit audio integration
Coordinates TTS engine, DataChannel communication, and audio track management
"""
import asyncio
import contextlib
import json
import time
from typing import Optional
import numpy as np
from livekit import rtc, agents

from ..config import get_config
from ..config.constants import AGENT_TTS_TRACK_ID
from ..logger import get_logger
from ..services.orchestrator_client import OrchestratorClient
from ..services.record_service_client import get_record_service_client
from ..services.tts_client import process_text_to_audio

logger = get_logger(__name__)

# audio-ingestion PLAN.md D3 only forwards tracks the agent *subscribes* to
# (on_track_subscribed never fires for the agent's own published track), so
# the agent's TTS output never reached record-service. Tapped directly here
# instead of via rtc.AudioStream.from_track(local_track) -- whether that even
# reads back a LocalAudioTrack's own frames is unverified, and this way the
# record-service session can open before the (lazily-published) LiveKit
# track exists at all, so `started_at` isn't skewed by TTS's lazy init.
#
# record-service is a dumb pass-through (D6): PCM chunks carry no timestamp,
# so received-byte-count IS the timeline. TTS only calls capture_frame() while
# actually speaking (no silence between turns), and in this interview flow
# gaps of 30s-4min (the candidate's turn) are the norm -- left unfilled, the
# recorded file would compress out all silence and drift out of sync with
# every other track once downstream merges them. This ticker tops up the
# exact elapsed gap on a coarse cadence; correctness doesn't depend on the
# interval (it always computes real elapsed time, never assumes a fixed
# duration), so it can stay cheap.
_SILENCE_TICK_INTERVAL_S = 2.0


class TTSManager:
    """
    Manages TTS functionality: model loading, DataChannel communication,
    and audio streaming to LiveKit room
    """
    
    def __init__(
        self,
        ctx: agents.JobContext,
        session_id: str,
        sample_rate: int = 16000,
        room_id: Optional[str] = None
    ):
        """
        Initialize TTS Manager

        Args:
            ctx: LiveKit job context
            session_id: Unique session identifier
            sample_rate: Audio sample rate in Hz -- MUST match the actual TTS
                service's real output rate (prod config: SAMPLE_RATE=16000),
                not just LiveKit/record-service's own assumption. Used both
                for the LiveKit publish track (AudioSource/AudioByteStream)
                and for what's declared to record-service -- a mismatch here
                distorts pitch/speed and skews recorded/derivative duration
                (previously hardcoded 24000 for "Kokoro", never verified
                against the real service; production bug, fixed 2026-08-06).
            room_id: Orchestrator's stable room UUID (falls back to session_id
                if registration hadn't completed -- same degrade path as
                event_handlers.py's record forwarding)
        """
        self.ctx = ctx
        self.session_id = session_id
        self.sample_rate = sample_rate
        self._room_id = room_id

        # Audio track management (persistent track)
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        self.track_published = False

        # record-service forwarding for the agent's own TTS output (see
        # module docstring above). Opened once in initialize(), lives for
        # the whole session.
        self._record_forwarder = None  # RecordForwarder | None
        self._record_track_id = AGENT_TTS_TRACK_ID
        self._record_last_sent_at: float = 0.0
        self._record_speaking = False
        self._record_ticker_task: Optional[asyncio.Task] = None
        # Wall-clock (time.time(), not monotonic -- needs to be comparable
        # to record-service's own started_at) anchor for reporting each TTS
        # utterance's [start, end] as a transcript segment (D3x), and the
        # epoch time the current/last utterance actually began sending real
        # frames (set in _publish_audio).
        self._record_session_started_epoch: float = 0.0
        self._record_utterance_start_epoch: float = 0.0
        self._orchestrator = OrchestratorClient.get_instance()
        
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

    async def _start_record_forwarding(self) -> None:
        """Best-effort: failure here must never break TTS itself (mirrors
        record_service_client's own contract -- None means "no recording
        this session", not an error to propagate)."""
        try:
            config = get_config()
            client = get_record_service_client()
            forwarder = await client.new_forwarder(
                room_id=self._room_id or self.session_id,
                track_id=self._record_track_id,
                participant_identity=config.livekit.agent_name,
                source="mic",
                sample_rate=self.sample_rate,
                channels=1,
            )
            if forwarder is None:
                return
            self._record_forwarder = forwarder
            self._record_last_sent_at = time.monotonic()
            self._record_session_started_epoch = time.time()
            self._record_ticker_task = asyncio.create_task(
                self._record_silence_ticker(), name="tts-record-silence-ticker"
            )
            logger.info("Started record-service forwarding for agent TTS track")
        except Exception as e:
            logger.error(f"Failed to start record-service forwarding for TTS: {e}", exc_info=True)

    async def _record_silence_ticker(self) -> None:
        """Tops up real elapsed silence on a coarse cadence so the recorded
        file's duration tracks wall-clock time through long gaps (the agent
        sits quiet for the candidate's whole answer). Skips entirely while
        _publish_audio is actively sending real frames, which does its own
        gap top-up right before it starts -- see module docstring."""
        try:
            while True:
                await asyncio.sleep(_SILENCE_TICK_INTERVAL_S)
                if self._record_speaking or self._record_forwarder is None:
                    continue
                self._send_record_silence_gap()
        except asyncio.CancelledError:
            pass

    def _send_record_silence_gap(self) -> None:
        """Non-blocking (send_audio only queues, see record_service_client.py)
        -- safe to call right before real audio without delaying it."""
        if self._record_forwarder is None:
            return
        now = time.monotonic()
        gap_s = now - self._record_last_sent_at
        self._record_last_sent_at = now
        if gap_s <= 0:
            return
        num_bytes = int(gap_s * self.sample_rate) * 2  # mono int16
        if num_bytes <= 0:
            return
        self._record_forwarder.send_audio(bytes(num_bytes))

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
                # Actually being asked to speak is the strongest signal this
                # session needs a recorded track at all (D3x) -- opened here,
                # not in initialize(), see that method's comment.
                await self._start_record_forwarding()
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

            # Report this utterance as a transcript segment (D3x) -- text is
            # already known (it came from orchestrator's own agent-control
            # call), so this replaces a Whisper STT round-trip for this
            # track entirely. Uses the epoch _publish_audio captured right
            # as real frames started (not request_start, which also
            # includes synthesis latency).
            await self._report_tts_transcript(text, audio_duration)

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
    
    async def _report_tts_transcript(self, text: str, audio_duration: float) -> None:
        """Best-effort: an orchestrator hiccup here must never fail TTS
        playback itself (mirrors _send_tts_status's own fire-and-forget
        posture)."""
        if self._record_forwarder is None or not self._record_session_started_epoch:
            return
        try:
            start = self._record_utterance_start_epoch - self._record_session_started_epoch
            end = start + audio_duration
            await self._orchestrator.report_tts_transcript(
                room_id=self._room_id or self.session_id,
                track_id=self._record_track_id,
                text=text,
                start=start,
                end=end,
            )
        except Exception as e:
            logger.warning(f"Failed to report TTS transcript segment: {e}")

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
        
        # Mark "speaking" before the silence ticker can interleave a stray
        # chunk mid-utterance, and flush whatever gap has accumulated since
        # the last chunk (real or silence) so it lands *before* real audio
        # in the forwarder's queue -- both calls are non-blocking (queue
        # puts only), so this adds no wall-clock delay to sending real audio.
        self._record_speaking = True
        self._record_utterance_start_epoch = time.time()
        self._send_record_silence_gap()
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
                # For 16000 Hz: 1600 samples = 100ms chunks
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
                    if self._record_forwarder is not None:
                        self._record_forwarder.send_audio(bytes(frame.data))
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
                        if self._record_forwarder is not None:
                            self._record_forwarder.send_audio(bytes(frame.data))
                        frame_count += 1
                    except Exception as e:
                        logger.error(f"Failed to capture flush frame: {e}")
                        raise

            logger.debug(f"Audio streaming completed successfully: {frame_count} frames sent")

        except Exception as e:
            logger.error(f"Failed to publish audio: {e}", exc_info=True)
            raise
        finally:
            # Real frames just sent already represent elapsed time up to
            # now -- reset the gap anchor so the ticker's next tick (or the
            # next utterance's pre-flush) only accounts for time from here.
            self._record_last_sent_at = time.monotonic()
            self._record_speaking = False

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

            # Stop the silence ticker and close the record-service session,
            # backfilling any trailing silence first so the file's duration
            # covers up to the actual end of the session, not just the last
            # real utterance.
            if self._record_ticker_task:
                self._record_ticker_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self._record_ticker_task
            if self._record_forwarder is not None:
                self._send_record_silence_gap()
                await self._record_forwarder.close()
                # No Whisper job ever runs for this track (skip_stt on
                # orchestrator's side, D3x) -- this is the only thing that
                # tells check_and_complete_room this track is done.
                try:
                    await self._orchestrator.report_tts_completed(
                        room_id=self._room_id or self.session_id,
                        track_id=self._record_track_id,
                    )
                except Exception as e:
                    logger.warning(f"Failed to report TTS track completed: {e}")

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
