import asyncio
import json
import websocket
import threading
import time
import numpy as np
from livekit import agents, rtc
from dotenv import load_dotenv
import uuid
load_dotenv()
# Whisper Live Server configuration
WHISPER_HOST = "localhost"
WHISPER_PORT = 9090
LANGUAGE = "en"
MODEL = "small"
USE_VAD = True
SAMPLE_RATE = 16000
CHANNELS = 1
# Audio processing settings (matching Whisper Live client)
CHUNK_SIZE = 4096 
class AudioChunkBuffer:
    """Buffer to accumulate audio data into chunks like the original Whisper Live client"""
    def __init__(self, chunk_size: int = CHUNK_SIZE):
        self.chunk_size = chunk_size
        self.buffer = bytearray()
    def add_data(self, data: bytes) -> list:
        """Add data to buffer and return complete chunks"""
        self.buffer.extend(data)
        chunks = []
        # Extract complete chunks
        while len(self.buffer) >= self.chunk_size:
            chunk = bytes(self.buffer[:self.chunk_size])
            chunks.append(chunk)
            self.buffer = self.buffer[self.chunk_size:]
        return chunks
    def bytes_to_float_array(self, data: bytes) -> np.ndarray:
        """Convert bytes to float array like the original client"""
        # Convert bytes to int16 array, then to float32 normalized to [-1, 1]
        audio_array = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
        return audio_array
    def get_remaining_data(self) -> bytes:
        """Get any remaining data in buffer"""
        if len(self.buffer) > 0:
            remaining = bytes(self.buffer)
            self.buffer = bytearray()
            return remaining
        return b''
class WhisperLiveChatClient:
    """
    Whisper Live client for LiveKit integration using websocket library (for chat messages)
    """
    def __init__(self, host, port, lang=None, model="small", use_vad=True, 
                 transcription_callback=None, participant_identity=None):
        self.host = host
        self.port = port
        self.language = lang
        self.model = model
        self.use_vad = use_vad
        self.transcription_callback = transcription_callback
        self.participant_identity = participant_identity
        self.uid = str(uuid.uuid4())
        self.recording = False
        self.waiting = False
        self.server_error = False
        self.last_response_received = None
        self.disconnect_if_no_response_for = 15
        self.server_backend = None
        self.last_segment = None
        self.last_received_segment = None
        # Add transcript tracking to avoid sending duplicates
        self.transcript = []  # Store completed segments
        self.last_sent_text = ""  # Track last sent text to avoid duplicates
        self.client_socket = None
        self.ws_thread = None
        self._stop_event = threading.Event()
        self.loop = None
    def connect(self):
        """Establish WebSocket connection to Whisper Live server (matching client.py)"""
        try:
            socket_url = f"ws://{self.host}:{self.port}"
            print(f"🔌 Connecting to Whisper Live server at {socket_url}")
            # Store current event loop for callback use
            self.loop = asyncio.get_event_loop()
            self.client_socket = websocket.WebSocketApp(
                socket_url,
                on_open=lambda ws: self.on_open(ws),
                on_message=lambda ws, message: self.on_message(ws, message),
                on_error=lambda ws, error: self.on_error(ws, error),
                on_close=lambda ws, close_status_code, close_msg: self.on_close(
                    ws, close_status_code, close_msg
                ),
            )
            # Start websocket client in a thread
            self.ws_thread = threading.Thread(target=self.client_socket.run_forever)
            self.ws_thread.daemon = True
            self.ws_thread.start()
            print(f"✅ WebSocket thread started for participant {self.participant_identity}")
            return True
        except Exception as e:
            print(f"❌ Failed to connect to Whisper Live server: {e}")
            return False
    def on_open(self, ws):
        """Callback function called when the WebSocket connection is successfully opened (matching client.py)"""
        print(f"✅ Connected to Whisper Live server for participant {self.participant_identity}")
        # Send __init__ial configuration (matching client.py)
        config = {
            "uid": self.uid,
            "language": self.language,
            "task": "transcribe",
            "model": self.model,
            "use_vad": self.use_vad,
            "send_last_n_segments": 10,
            "no_speech_thresh": 0.45,
            "clip_audio": False,
            "same_output_threshold": 10,
            "enable_translation": False,
            "target_language": "en",
        }
        ws.send(json.dumps(config))
    def on_message(self, ws, message):
        """Callback function called when a message is received from the server (matching client.py)"""
        try:
            message_data = json.loads(message)
            if self.uid != message_data.get("uid"):
                print(f"❌ Invalid client uid for participant {self.participant_identity}")
                return
            # Handle status messages
            if "status" in message_data:
                status = message_data["status"]
                if status == "WAIT":
                    self.waiting = True
                    print(f"⏳ Server is full. Wait time: {round(message_data.get('message', 0))} minutes.")
                elif status == "ERROR":
                    print(f"❌ Server error: {message_data.get('message')}")
                    self.server_error = True
                elif status == "WARNING":
                    print(f"⚠️ Server warning: {message_data.get('message')}")
                return
            # Handle disconnect message
            if message_data.get("message") == "DISCONNECT":
                print(f"🔌 Server disconnected participant {self.participant_identity}")
                self.recording = False
                return
            # Handle server ready message
            if message_data.get("message") == "SERVER_READY":
                self.last_response_received = time.time()
                self.recording = True
                self.server_backend = message_data.get("backend", "unknown")
                print(f"🚀 Whisper server ready with backend {self.server_backend}")
                return
            # Handle language detection
            if "language" in message_data:
                detected_lang = message_data.get("language")
                lang_prob = message_data.get("language_prob", 0)
                print(f"🌐 Detected language: {detected_lang} (prob: {lang_prob:.2f})")
                return
            # Handle transcription segments
            if "segments" in message_data:
                self._process_segments_sync(message_data["segments"])
        except json.JSONDecodeError as e:
            print(f"❌ Failed to parse message from Whisper server: {e}")
        except Exception as e:
            print(f"❌ Error handling server message: {e}")
    def on_error(self, ws, error):
        """Callback function called when WebSocket error occurs (matching client.py)"""
        print(f"❌ WebSocket Error for participant {self.participant_identity}: {error}")
        self.server_error = True
    def on_close(self, ws, close_status_code, close_msg):
        """Callback function called when WebSocket connection is closed (matching client.py)"""
        print(f"🔌 WebSocket connection closed for participant {self.participant_identity}: {close_status_code}: {close_msg}")
        self.recording = False
        self.waiting = False
    def _process_segments_sync(self, segments):
        """Process transcription segments from Whisper Live (synchronous version with duplicate filtering)"""
        if not segments:
            return
        # Process segments similar to client.py
        new_text_parts = []
        current_text = ""
        for i, seg in enumerate(segments):
            text = seg.get("text", "").strip()
            if not text:
                continue
            # Add to current text parts (avoiding immediate duplicates)
            if not new_text_parts or new_text_parts[-1] != text:
                new_text_parts.append(text)
            # Track last segment (incomplete)
            if i == len(segments) - 1 and not seg.get("completed", False):
                self.last_segment = seg
            elif seg.get("completed", False):
                # This is a completed segment - add to transcript if new
                if (not self.transcript or 
                    float(seg.get('start', 0)) >= float(self.transcript[-1].get('end', 0))):
                    self.transcript.append(seg)
        # Update last response time
        if segments:
            if self.last_received_segment is None or self.last_received_segment != segments[-1].get("text"):
                self.last_response_received = time.time()
                self.last_received_segment = segments[-1].get("text")
        # Only send if we have new content
        if new_text_parts:
            current_text = " ".join(new_text_parts)
            # Only send if text is different from last sent
            if current_text.strip() and current_text.strip() != self.last_sent_text.strip():
                self.last_sent_text = current_text
                try:
                    if self.loop and not self.loop.is_closed():
                        asyncio.run_coroutine_threadsafe(
                            self.transcription_callback(current_text, segments), 
                            self.loop
                        )
                except Exception as e:
                    print(f"⚠️ Transcription callback error: {e}")
    def send_audio(self, audio_data):
        """Send audio data to Whisper Live server (matching client.py)"""
        if self.client_socket and self.recording:
            try:
                self.client_socket.send(audio_data, websocket.ABNF.OPCODE_BINARY)
            except Exception as e:
                print(f"❌ Failed to send audio data: {e}")
    def disconnect(self):
        """Close WebSocket connection (matching client.py)"""
        try:
            self._stop_event.set()
            if self.client_socket:
                self.client_socket.close()
            if self.ws_thread:
                self.ws_thread.join(timeout=5)
        except Exception as e:
            print(f"⚠️ Error during disconnect: {e}")
async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()
    # Dictionary to track active transcription clients
    active_clients = {}
    async def send_chat_message(text: str, participant_identity: str, participant_name: str = "Speaker"):
        """Send chat message via LiveKit chat topic with retry logic"""
        max_retries = 3
        retry_delay = 1.0
        for attempt in range(max_retries):
            try:
                # Check if room is still connected
                if ctx.room.connection_state != rtc.ConnectionState.CONN_CONNECTED:
                    print(f"⚠️ Room not connected (state: {ctx.room.connection_state}), skipping message")
                    return False
                # Send as JSON data to chat topic
                await ctx.room.local_participant.send_text(
                    text,
                    topic="lk.chat"  
                )
                print(f"💬 Chat message sent for {participant_identity}: {text[:50]}{'...' if len(text) > 50 else ''}")
                return True
            except Exception as e:
                print(f"❌ Error sending chat message (attempt {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    print(f"❌ Failed to send chat message after {max_retries} attempts")
                    return False
    def create_transcription_callback(participant_identity: str, participant_name: str):
        """Create transcription callback for a specific participant"""
        async def transcription_callback(text: str, segments: list):
            """Handle transcription results from Whisper Live and send to chat"""
            if not text or not text.strip():
                return
            # Determine if this is a final transcription
            is_final = False
            if segments:
                last_segment = segments[-1]
                is_final = last_segment.get("completed", False)
            # Only send completed transcriptions to chat to avoid spam
            if is_final:
                # Send chat message to LiveKit room
                await send_chat_message(
                    text=text.strip(),
                    participant_identity=participant_identity,
                    participant_name=participant_name
                )
        return transcription_callback
    async def manage_speaker_transcription(track: rtc.RemoteAudioTrack, participant: rtc.RemoteParticipant):
        """Manage transcription for a specific speaker using Whisper Live"""
        speaker_id = participant.identity
        speaker_name = participant.name or f"Speaker {speaker_id}"
        print(f"🎙️ Starting transcription for participant {speaker_id}")
        # Create transcription callback
        callback = create_transcription_callback(speaker_id, speaker_name)
        # Create Whisper Live client
        whisper_client = WhisperLiveChatClient(
            host=WHISPER_HOST,
            port=WHISPER_PORT,
            lang=LANGUAGE,
            model=MODEL,
            use_vad=USE_VAD,
            transcription_callback=callback,
            participant_identity=speaker_id
        )
        # Connect to Whisper Live server
        if not whisper_client.connect():
            print(f"❌ Failed to connect to Whisper Live for participant {speaker_id}")
            return
        # Store client reference
        active_clients[speaker_id] = whisper_client
        # Wait for server to be ready
        max_wait = 10  # seconds
        wait_time = 0
        while not whisper_client.recording and not whisper_client.server_error and not whisper_client.waiting:
            await asyncio.sleep(0.1)
            wait_time += 0.1
            if wait_time > max_wait:
                print(f"⏰ Timeout waiting for Whisper server for participant {speaker_id}")
                break
        if whisper_client.server_error or whisper_client.waiting:
            print(f"❌ Cannot start transcription for participant {speaker_id}")
            whisper_client.disconnect()
            active_clients.pop(speaker_id, None)
            return
        print(f"🚀 Transcription ready for participant {speaker_id}")
        # Create audio chunk buffer (like original Whisper Live client)
        chunk_buffer = AudioChunkBuffer(CHUNK_SIZE)
        # Statistics tracking
        frame_count = 0
        chunks_sent = 0
        last_stats_time = time.time()
        try:
            # Stream audio to Whisper Live
            stream = rtc.AudioStream.from_track(track=track, sample_rate=16000, num_channels=1)
            async for event in stream:
                if speaker_id not in active_clients:
                    break
                frame = event.frame
                frame_count += 1
                # Add raw frame data to chunk buffer
                raw_data = bytes(frame.data)  # Keep as int16 bytes
                chunks = chunk_buffer.add_data(raw_data)
                # Process each complete chunk
                for chunk_data in chunks:
                    # Convert to float array like original client
                    audio_array = chunk_buffer.bytes_to_float_array(chunk_data)
                    audio_bytes = audio_array.tobytes()
                    # Send audio data to Whisper Live
                    whisper_client.send_audio(audio_bytes)
                    chunks_sent += 1
                # Print periodic stats (every 10 seconds)
                current_time = time.time()
                if current_time - last_stats_time > 10:
                    duration = frame_count * 1024 / SAMPLE_RATE  # Assuming 1024 samples per frame
                    print(f"📈 {speaker_id}: {frame_count} frames, {chunks_sent} chunks sent, ~{duration:.1f}s audio processed")
                    last_stats_time = current_time
            # Send any remaining data in buffer
            remaining_data = chunk_buffer.get_remaining_data()
            if remaining_data:
                audio_array = chunk_buffer.bytes_to_float_array(remaining_data)
                audio_bytes = audio_array.tobytes()
                whisper_client.send_audio(audio_bytes)
                chunks_sent += 1
                print(f"📤 Sent final chunk for {speaker_id} ({len(remaining_data)} bytes)")
            print(f"⏹️ Audio stream ended for participant {speaker_id} (total chunks sent: {chunks_sent})")
        except Exception as e:
            print(f"❌ Error during audio streaming for participant {speaker_id}: {e}")
        finally:
            # Clean up
            if speaker_id in active_clients:
                whisper_client.disconnect()
                active_clients.pop(speaker_id, None)
                print(f"🧹 Cleaned up transcription for participant {speaker_id}")
    def on_track_subscribed(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        """Handle new audio track subscription"""
        # publication parameter is required by LiveKit but not used in this handler
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            print(f"🎵 New audio track from participant {participant.identity}")
            asyncio.create_task(manage_speaker_transcription(track, participant))
    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        """Handle participant disconnection"""
        speaker_id = participant.identity
        if speaker_id in active_clients:
            print(f"👋 Participant {speaker_id} disconnected, cleaning up transcription")
            active_clients[speaker_id].disconnect()
            active_clients.pop(speaker_id, None)
    def on_disconnected():
        """Handle room disconnection"""
        print("🔌 Room disconnected, cleaning up all transcription clients")
        for client in active_clients.values():
            client.disconnect()
        active_clients.clear()
        disconnected.set()
    # Set up event handlers
    ctx.room.on("track_subscribed", on_track_subscribed)
    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("disconnected", on_disconnected)
    # Set agent name
    await ctx.room.local_participant.set_name("Whisper Live Chat Agent")
    # Send welcome message to chat
    async def send_welcome_message():
        await asyncio.sleep(2)  # Wait for stable connection
        await send_chat_message(
            text="🤖 Whisper Live chat agent is ready! Transcriptions will appear in chat.",
            participant_identity="agent",
            participant_name="Chat Agent"
        )
    asyncio.create_task(send_welcome_message())
    print("🎯 Whisper Live Chat Agent ready and waiting for participants...")
    await disconnected.wait()
if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))