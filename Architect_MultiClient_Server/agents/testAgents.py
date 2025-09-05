import asyncio
import json
import websockets
import time
import numpy as np
from livekit import agents, rtc
from dotenv import load_dotenv

load_dotenv()

# WebSocket Server configuration (Vosk-style)
WEBSOCKET_HOST = "localhost"
WEBSOCKET_PORT = 8000
TRANSCRIPT = True
TRANSLATION = True

SAMPLE_RATE = 16000
CHANNELS = 1


class WebSocketTranscriptionClient:
    """
    Async WebSocket client that connects to ws://host:port/ws/vosk/ and streams raw PCM16 bytes.
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

    async def connect(self):
        """Establish WebSocket connection to transcription server"""
        uri = (
            f"ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}/ws/vosk/"
            f"?client_id={self.client_id}&session_id={self.session_id}"
            f"&transcript={str(self.transcript).lower()}&translation={str(self.translation).lower()}"
        )
        self.uri = uri
        print(f"Connecting to transcription server...\n  uri={uri}\n  host={WEBSOCKET_HOST} port={WEBSOCKET_PORT}\n  participant={self.participant_identity}")

        try:
            self.websocket = await websockets.connect(
                uri,
                ping_interval=20,
                ping_timeout=10,
                close_timeout=10,
                max_size=None,
            )
            self.connected = True

            # Start receiving messages
            self.receive_task = asyncio.create_task(self._receive_messages())

            print(f"WebSocket connected for participant {self.participant_identity}")
            return True

        except websockets.exceptions.InvalidStatusCode as e:
            status = getattr(e, "status_code", None)
            print(f"Failed to connect: HTTP status={status} uri={uri}")
            return False
        except websockets.exceptions.InvalidHandshake as e:
            print(f"Failed to connect: invalid handshake uri={uri} error={e}")
            return False
        except ConnectionRefusedError as e:
            print(f"Failed to connect: connection refused uri={uri} error={e}")
            return False
        except OSError as e:
            print(f"Failed to connect: network error uri={uri} error={e}")
            return False
        except Exception as e:
            print(f"Failed to connect: unexpected error uri={uri} error={repr(e)}")
            return False

    async def reconnect(self, max_attempts: int = 3, base_delay: float = 0.5) -> bool:
        """Try to reconnect with exponential backoff without losing loop context."""
        for attempt in range(1, max_attempts + 1):
            try:
                print(f"Reconnecting attempt {attempt}/{max_attempts} to {self.uri}")
                self.websocket = await websockets.connect(
                    self.uri,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=10,
                    max_size=None,
                )
                self.connected = True
                # restart receiver
                self.receive_task = asyncio.create_task(self._receive_messages())
                print("Reconnected WebSocket successfully")
                return True
            except Exception as e:
                delay = base_delay * (2 ** (attempt - 1))
                print(f"Reconnect failed (attempt {attempt}): {e}. Retrying in {delay:.2f}s")
                await asyncio.sleep(delay)
        return False

    async def _receive_messages(self):
        """Receive and process messages from transcription server"""
        try:
            async for message in self.websocket:
                print(f"{message}")
                if self.transcription_callback:
                    try:
                        await self.transcription_callback(message)
                    except Exception as e:
                        print(f"Transcription callback error: {e}")
        except websockets.exceptions.ConnectionClosed as e:
            code = getattr(e, "code", None)
            reason = getattr(e, "reason", None)
            print(f"WebSocket connection closed for {self.participant_identity}: code={code} reason={reason}")
        except Exception as e:
            print(f"Error receiving messages: {e}")
        finally:
            # Ensure socket is closed on exit
            try:
                if self.websocket and not self.websocket.closed:
                    await self.websocket.close()
            except Exception:
                pass
            self.connected = False

    async def send_audio(self, audio_bytes: bytes):
        """Send raw PCM16 bytes to transcription server"""
        if not self.connected or not self.websocket:
            # Try to reconnect before sending
            ok = await self.reconnect()
            if not ok:
                return
        if self.websocket and self.connected:
            try:
                await self.websocket.send(audio_bytes)
            except Exception as e:
                print(f"Failed to send audio data: {e}")
                self.connected = False
                # Proactively disconnect to release resources
                try:
                    await self.disconnect()
                except Exception:
                    pass

    async def disconnect(self):
        """Close WebSocket connection"""
        try:
            if self.receive_task:
                self.receive_task.cancel()
                try:
                    await self.receive_task
                except asyncio.CancelledError:
                    pass
            if self.websocket:
                await self.websocket.close()
            self.connected = False
        except Exception as e:
            print(f"Error during disconnect: {e}")


async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()

    # Active clients keyed by participant id (simple case)
    active_clients = {}

    async def safe_disconnect_all():
        tasks = [client.disconnect() for client in active_clients.values()]
        if tasks:
            try:
                await asyncio.gather(*tasks, return_exceptions=True)
            except Exception:
                pass
        active_clients.clear()

    def session_id_from_room() -> str:
        return ctx.room.name or ctx.room.sid or f"room_{int(time.time())}"

    def create_transcription_callback(participant_identity: str):
        async def transcription_callback(message: str):
            # You can forward via data channel if needed; currently just print
            pass
        return transcription_callback

    async def manage_speaker_transcription(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        """For each audio track, open a WS client to Vosk backend and stream bytes."""
        speaker_id = participant.identity
        sid = session_id_from_room()
        print(f"Starting WS transcription for participant {speaker_id} (session={sid})")

        ws_client = WebSocketTranscriptionClient(
            client_id=speaker_id,
            session_id=sid,
            transcript=TRANSCRIPT,
            translation=TRANSLATION,
            transcription_callback=create_transcription_callback(speaker_id),
            participant_identity=speaker_id,
        )

        if not await ws_client.connect():
            print(f"Failed to connect WS for participant {speaker_id}")
            return

        active_clients[speaker_id] = ws_client

        # Stream audio frames as raw bytes
        frames = 0
        chunks = 0
        last_log = time.time()
        try:
            stream = rtc.AudioStream.from_track(track=track, sample_rate=SAMPLE_RATE, num_channels=CHANNELS)
            async for event in stream:
                if speaker_id not in active_clients:
                    print(f"{speaker_id}: streaming stopped because client removed from active_clients")
                    break
                if not ws_client.connected:
                    print(f"{speaker_id}: WebSocket disconnected, attempting reconnect while continuing stream")
                    await ws_client.reconnect()
                frame = event.frame
                frames += 1
                # frame.data is already PCM16 bytes
                await ws_client.send_audio(bytes(frame.data))
                print(f"{speaker_id}: sent frame data tata")
                chunks += 1
                now = time.time()
                if now - last_log > 10:
                    print(f"{speaker_id}: sent {frames} frames (~{chunks} chunks)")
                    last_log = now
            print(f"Audio stream ended for participant {speaker_id} (total chunks sent: {chunks})")
        except Exception as e:
            print(f"Error during audio streaming for {speaker_id}: {e}")
        finally:
            if speaker_id in active_clients:
                await ws_client.disconnect()
                active_clients.pop(speaker_id, None)
                print(f"🧹 Cleaned up transcription for participant {speaker_id}")

    def on_track_unsubscribed(track: rtc.RemoteTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            pid = participant.identity
            print(f"Audio track unsubscribed for participant {pid}; stopping stream")
            client = active_clients.get(pid)
            if client:
                asyncio.create_task(client.disconnect())
                active_clients.pop(pid, None)

    def on_track_subscribed(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"New audio track from participant {participant.identity}")
            asyncio.create_task(manage_speaker_transcription(track, publication, participant))

    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        pid = participant.identity
        if pid in active_clients:
            print(f"👋 Participant {pid} disconnected, cleaning up")
            asyncio.create_task(active_clients[pid].disconnect())
            active_clients.pop(pid, None)

    async def on_disconnected():
        print("Room disconnected, cleaning up all WS clients")
        tasks = [client.disconnect() for client in active_clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        active_clients.clear()
        disconnected.set()

    ctx.room.on("track_subscribed", on_track_subscribed)
    ctx.room.on("track_unsubscribed", on_track_unsubscribed)
    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

    await ctx.room.local_participant.set_name("Vosk WS Transcription Agent")

    print("🎤 Vosk-style WS Agent ready and waiting for participants...")
    try:
        await disconnected.wait()
    finally:
        await safe_disconnect_all()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))