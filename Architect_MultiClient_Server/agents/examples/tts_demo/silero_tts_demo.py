"""
Silero TTS LiveKit Agent - Join room và phát voice từ WebSocket server
Refactored version with modular architecture (fixed connect / metadata / args)
"""
import asyncio
import time
import os
from typing import Optional
import numpy as np
from livekit import rtc
from livekit.agents import JobContext, WorkerOptions, cli
from dotenv import load_dotenv
from livekit import api

# Import custom modules (giữ nguyên theo project của bạn)
from tts_engine import TTSEngine
from ws_tts_client import WebSocketTTSClient

load_dotenv()

class SileroTTSAgent:
    """Agent phát voice vào LiveKit room từ WebSocket server"""

    def __init__(
        self,
        ctx: JobContext,
        session_id: str,
        ws_url: str,
        sample_rate: int = 48000
    ):
        """
        Initialize Silero TTS Agent

        Args:
            ctx: LiveKit job context
            session_id: Unique session identifier
            ws_url: WebSocket server URL
            sample_rate: Audio sample rate in Hz
        """
        self.ctx = ctx
        self.session_id = session_id
        self.sample_rate = sample_rate

        # Initialize TTS engine
        self.tts_engine = TTSEngine(sample_rate=sample_rate)

        # Initialize WebSocket client (on_text_received is async-safe)
        self.ws_client = WebSocketTTSClient(
            session_id=session_id,
            ws_url=ws_url,
            on_text_received=self._on_text_received
        )

        # Audio track (persistent)
        self.audio_source: Optional[rtc.AudioSource] = None
        self.audio_track: Optional[rtc.LocalAudioTrack] = None
        self.track_published = False

    async def setup_audio_track(self) -> bool:
        """
        Setup persistent audio track (publish once)

        Returns:
            True if successful, False otherwise
        """
        try:
            # Create audio source (mono)
            self.audio_source = rtc.AudioSource(self.sample_rate, num_channels=1)

            # Create local audio track. API may differ slightly across SDKs;
            # adjust create_audio_track call if necessary.
            self.audio_track = rtc.LocalAudioTrack.create_audio_track(
                "tts-announcements",
                self.audio_source
            )

            # Publish track on local participant from ctx.room (ensure ctx.room exists)
            local_participant = self.ctx.room.local_participant
            options = rtc.TrackPublishOptions()
            options.source = rtc.TrackSource.SOURCE_MICROPHONE

            publication = await local_participant.publish_track(
                self.audio_track,
                options
            )

            self.track_published = True
            print(f"✅ Published audio track: {publication.sid}")
            return True

        except Exception as e:
            print(f"❌ Failed to setup audio track: {e}")
            import traceback
            traceback.print_exc()
            self.track_published = False
            return False

    async def start(self) -> bool:
        """
        Start the agent: load model and connect to WebSocket

        Returns:
            True if successful, False otherwise
        """
        # Load TTS model (assume load() may be async)
        try:
            if hasattr(self.tts_engine, "load") and asyncio.iscoroutinefunction(self.tts_engine.load):
                ok = await self.tts_engine.load()
            else:
                # synchronous fallback
                ok = self.tts_engine.load()
            if not ok:
                print("❌ Failed to load TTS model")
                return False
        except Exception as e:
            print(f"❌ Exception while loading TTS model: {e}")
            import traceback
            traceback.print_exc()
            return False

        # Connect to WebSocket client
        try:
            if not await self.ws_client.connect():
                print("❌ Failed to connect to WebSocket")
                return False
        except Exception as e:
            print(f"❌ Exception connecting to WebSocket: {e}")
            import traceback
            traceback.print_exc()
            return False

        print("✅ Agent started (TTS model loaded + WS connected)")
        return True

    async def _on_text_received(self, text: str) -> None:
        """
        Callback when text is received from WebSocket

        Args:
            text: Text to synthesize and play
        """
        # ensure not blocking the WS thread
        asyncio.create_task(self._process_tts_request(text))

    async def _process_tts_request(self, text: str) -> None:
        """
        Process TTS request: synthesize and publish audio

        Args:
            text: Text to synthesize
        """
        try:
            start_time = time.time()

            # Step 1: Synthesize
            print(f"   🎯 Step 1: Synthesizing...")
            print(f"   🔊 Text: \"{text[:50]}{'...' if len(text) > 50 else ''}\"")

            # Synthesize (assume synchronous returning np.ndarray float32 [-1,1])
            audio_data = self.tts_engine.synthesize(text)

            synthesis_time = time.time() - start_time
            print(f"   ⏱️ Synthesis time: {synthesis_time:.2f}s")

            # Step 2: Publish to room
            print(f"   🎯 Step 2: Publishing audio to room...")
            await self._publish_audio(audio_data)

            # Step 3: Calculate stats
            total_time = time.time() - start_time
            audio_duration = self.tts_engine.get_audio_duration(audio_data)

            print(f"   ✅ Audio playback completed!")
            print(f"   📊 Stats:")
            print(f"      - Synthesis: {synthesis_time:.2f}s")
            print(f"      - Duration: {audio_duration:.2f}s")
            print(f"      - Total: {total_time:.2f}s")

            # Step 4: Send completion status
            print(f"   📤 Sending status to server...")
            await self.ws_client.send_status("completed", {
                "text_length": len(text),
                "audio_duration": audio_duration,
                "synthesis_time": synthesis_time,
                "total_processing_time": total_time
            })
            print(f"   ✅ Status sent")

        except Exception as e:
            print(f"❌ Error processing TTS request: {e}")
            import traceback
            traceback.print_exc()

            # Send error status (best-effort)
            try:
                await self.ws_client.send_status("error", {"error": str(e)})
            except Exception:
                pass

    async def _publish_audio(self, audio_data: np.ndarray) -> None:
        """
        Publish audio to LiveKit room

        Args:
            audio_data: Audio samples (float32, [-1.0, 1.0])
        """
        if not self.track_published or not self.audio_source:
            raise RuntimeError("Audio track not ready")

        # Convert float32 to int16
        audio_int16 = (audio_data * 32767).astype(np.int16)

        # Stream with 10ms chunks
        chunk_size = self.sample_rate // 100
        total_chunks = max(1, len(audio_int16) // chunk_size)

        print(f"   📡 Streaming ~{total_chunks} chunks ({len(audio_int16) / self.sample_rate:.2f}s)")

        for i in range(0, len(audio_int16), chunk_size):
            if not self.ws_client.is_running:
                print("   ⚠️ Streaming interrupted")
                break

            chunk = audio_int16[i:i + chunk_size]

            # Pad last chunk if needed
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)), mode='constant')

            # Create and send frame
            frame = rtc.AudioFrame(
                data=chunk.tobytes(),
                sample_rate=self.sample_rate,
                num_channels=1,
                samples_per_channel=len(chunk)
            )

            await self.audio_source.capture_frame(frame)

        # Ensure last frames are flushed
        await asyncio.sleep(0.1)
        print("   ✅ Audio streaming completed")

    async def wait_until_stopped(self) -> None:
        """Wait until agent is stopped"""
        await self.ws_client.wait_until_disconnected()

    async def stop(self) -> None:
        """Stop the agent and cleanup resources"""
        print("🛑 Stopping agent...")

        # Disconnect WebSocket
        try:
            await self.ws_client.disconnect()
        except Exception:
            pass

        # Flush audio buffer (play tiny silence)
        if self.audio_source:
            try:
                silent_frame = rtc.AudioFrame(
                    data=np.zeros(self.sample_rate // 100, dtype=np.int16).tobytes(),
                    sample_rate=self.sample_rate,
                    num_channels=1,
                    samples_per_channel=self.sample_rate // 100
                )
                await self.audio_source.capture_frame(silent_frame)
            except Exception:
                pass

        # Cleanup TTS engine
        try:
            self.tts_engine.cleanup()
        except Exception:
            pass

        print("✅ Agent stopped")


async def entrypoint(ctx: JobContext):
    """LiveKit agent entrypoint"""
    print("=" * 80)
    print("🚀 Silero TTS WebSocket Agent Starting")
    print("=" * 80)

    # --- 1) connect first (important!) ---
    await ctx.connect()
    print(f"✅ Connected to room: {ctx.room.name}")
    # Lấy thông tin cần thiết
    room_name = ctx.room.name
    participant_identity = ctx.room.local_participant.identity

    # Dùng API client để update
    lkapi = api.LiveKitAPI(
        url=os.getenv("LIVEKIT_HTTP_URL"),
        api_key=os.getenv("LIVEKIT_API_KEY"),
        api_secret=os.getenv("LIVEKIT_API_SECRET"),
    )

    await lkapi.room.update_participant_metadata(
        api.UpdateParticipantMetadataRequest(
            room=room_name,
            identity=participant_identity,
            metadata='{"display_name": "TTS Test Client"}',
        )
    )

    await lkapi.aclose()

    # Get session_id from room name
    session_id = ctx.room.name or "default_session"
    print(f"📍 Session ID: {session_id}")

    # WS URL (cấu hình từ env hoặc mặc định)
    ws_url = os.getenv("TTS_WS_URL", "ws://localhost:9000/ws")

    # Create agent with ctx and ws_url
    agent = SileroTTSAgent(ctx=ctx, session_id=session_id, ws_url=ws_url)

    try:
        # Setup audio track (now ctx.room is available so publish works)
        ok = await agent.setup_audio_track()
        if not ok:
            raise RuntimeError("Failed to setup audio track")

        # Start agent (load model, connect WebSocket)
        if not await agent.start():
            raise RuntimeError("Agent start failed")

        print("=" * 80)
        print("✅ Agent Ready - Listening for messages from WebSocket server")
        print("=" * 80)

        # Keep agent running until stopped
        await agent.wait_until_stopped()

    except KeyboardInterrupt:
        print("\n⚠️ Interrupted by user")
    except Exception as e:
        print(f"❌ Error in agent: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Ensure cleanup
        await agent.stop()
        print("=" * 80)
        print("👋 Agent shutting down")
        print("=" * 80)


if __name__ == "__main__":
    # Run as LiveKit agent
    cli.run_app(WorkerOptions(entrypoint_fnc=entrypoint))
