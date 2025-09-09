import asyncio
import json
import websockets
import time
import numpy as np
from livekit import agents, rtc
import os
from dotenv import load_dotenv
import logging

load_dotenv()

# Configure logging with custom format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

# Set specific log levels for different loggers
logging.getLogger('websockets').setLevel(logging.WARNING)  # Reduce websocket noise
logging.getLogger('asyncio').setLevel(logging.WARNING)    # Reduce asyncio noise

# Create metrics logger
metrics_logger = logging.getLogger('metrics')
metrics_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

def get_logger(name: str) -> logging.Logger:
    """Get logger with consistent formatting"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)  # Ensure new loggers also have INFO level
    return logger

# WebSocket Server configuration (Vosk-style)
WEBSOCKET_HOST = os.getenv("WEBSOCKET_HOST", "server")
WEBSOCKET_PORT = int(os.getenv("WEBSOCKET_PORT", "8000"))
TRANSCRIPT = True
TRANSLATION = True

SAMPLE_RATE = 16000
CHANNELS = 1

# Optimization parameters
BATCH_SIZE = 1  # Number of frames to batch before sending
SEND_DELAY = 0.005  # 5ms delay between sends to prevent server overload
MAX_BUFFER_SIZE = 1024 * 16  # 16KB max buffer size
RECONNECT_MAX_ATTEMPTS = 3
RECONNECT_BASE_DELAY = 1.0


class TranscriptManager:
    """Manages transcript entries and LiveKit data channel communication"""
    
    def __init__(self, ctx: agents.JobContext):
        self.ctx = ctx
        self.logger = get_logger("transcript_manager")
        # Per-participant incremental sequence for client-side ordering
        self._seq_by_participant = {}
        # Anchor timestamp per participant (first transcript seen)
        self._start_ms_by_participant = {}
    
    async def send_transcript_entry(
        self,
        text: str,
        participant_identity: str,
        participant_name: str = "Speaker",
        is_final: bool = True,
        segments: list | None = None,
        language: str | None = None,
        seq: int | None = None,
    ):
        """Send transcript payload (flat schema) via Data Channel"""
        try:
            # Compute sequence if not provided
            if seq is None:
                current = self._seq_by_participant.get(participant_identity, 0) + 1
                self._seq_by_participant[participant_identity] = current
                seq = current
            # Anchor timestamp to first time we receive transcript for this participant
            if participant_identity not in self._start_ms_by_participant:
                self._start_ms_by_participant[participant_identity] = int(time.time() * 1000)
            timestamp_ms = self._start_ms_by_participant[participant_identity]

            # Flat schema for flexible client rendering
            transcript_entry = {
                "participantIdentity": participant_identity,
                "participantName": participant_name,
                "seq": seq,
                "isFinal": is_final,
                "language": language,
                "text": text,
                "segments": segments or [],
                "timestamp": timestamp_ms,
            }
            
            transcript_data = {
                "type": "transcript",
                "entry": transcript_entry
            }
            data = json.dumps(transcript_data).encode("utf-8")
            # Only log detailed transcript info at debug level
            self.logger.debug(f"Published transcript: {text[:50]}..." if len(text) > 50 else f"Published transcript: {text}")
            await self.ctx.room.local_participant.publish_data(
                data,
                reliable=True,
                topic="transcript"
            )
            return True
            
        except Exception as e:
            self.logger.error(f"Error sending transcript: {e}")
            return False
    
    def create_transcription_callback(self, participant_identity: str, participant_name: str):
        """Create transcription callback for a specific participant"""
        
        async def transcription_callback(text: str, segments: list = None):
            """Handle transcription results from server"""
            if not text or not text.strip():
                return
            
            # Since server only sends text, create a single segment
            if segments is None:
                segments = [{
                    "text": text.strip(),
                    "start": 0.0,
                    "end": 0.0,
                    "completed": True
                }]
            
            # Determine if this is a final transcription
            is_final = True  # Server sends final text, so always final
            
            # Optional: pick language if server attached it in segments (keep None if absent)
            lang = None

            # Prepare convenience text (already provided), forward raw segments to client
            await self.send_transcript_entry(
                text=text.strip(),
                participant_identity=participant_identity,
                participant_name=participant_name,
                is_final=is_final,
                segments=segments,
                language=lang,
            )
        
        return transcription_callback
    
    async def send_welcome_message(self):
        """Send welcome message when agent is ready"""
        await asyncio.sleep(2)  # Wait for stable connection
        await self.send_transcript_entry(
            text="Vosk transcription agent is ready!",
            participant_identity="agent",
            participant_name="Transcription Agent",
            is_final=True
        )


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


async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()

    # Initialize transcript manager for data channel communication
    transcript_manager = TranscriptManager(ctx)

    # Active clients with better management
    active_clients = {}
    cleanup_lock = asyncio.Lock()

    async def safe_disconnect_all():
        """Safely disconnect all clients with proper cleanup"""
        async with cleanup_lock:
            if not active_clients:
                return
                
            logger.info(f"Disconnecting {len(active_clients)} active clients")
            
            # Create disconnect tasks
            tasks = []
            for client_id, client in list(active_clients.items()):
                tasks.append(client.disconnect())
            
            # Wait for all disconnections with timeout
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), 
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Some clients took too long to disconnect")
                    
            active_clients.clear()
            logger.info("All clients disconnected")

    def session_id_from_room() -> str:
        """Generate session ID from room info"""
        return ctx.room.name or ctx.room.sid or f"room_{int(time.time())}"

    def create_transcription_callback(participant_identity: str, transcript_manager: TranscriptManager):
        """Create callback for handling transcription results"""
        async def transcription_callback(message: str):
            try:
                # Parse and handle transcription message
                data = json.loads(message) if message.startswith('{') else {"text": message}
                text = data.get("text", "").strip()
                
                # Only log if there's actual text content
                if text:
                    is_final = bool(data.get("is_final", False))
                    status = "FINAL" if is_final else "interim"
                    logger.info(f"[{participant_identity}] {status}: {text}")
                    # Dùng thời điểm nhận data làm start/end
                    receive_time = time.time()

                    segments = [{
                        "text": text,
                        "start": receive_time,
                        "end": receive_time,
                        "completed": bool(data.get("is_final", False))
                    }]
                    
                    # Use transcript manager to send via data channel
                    await transcript_manager.send_transcript_entry(
                        text=text,
                        participant_identity=participant_identity,
                        participant_name=participant_identity,
                        is_final=bool(data.get("is_final", False)),
                        segments=segments,
                        language=None
                    )
                
            except Exception as e:
                logger.error(f"Error processing transcription for {participant_identity}: {e}")
                
        return transcription_callback


    async def manage_speaker_transcription(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        """Optimized audio streaming with better resource management"""
        speaker_id = participant.identity
        sid = session_id_from_room()
        
        logger.info(f"Starting transcription for {speaker_id} (session={sid})")

        # Create WebSocket client
        ws_client = WebSocketTranscriptionClient(
            client_id=speaker_id,
            session_id=sid,
            transcript=TRANSCRIPT,
            translation=TRANSLATION,
            transcription_callback=create_transcription_callback(speaker_id, transcript_manager),
            participant_identity=speaker_id,
        )

        # Connect to transcription server
        if not await ws_client.connect():
            logger.error(f"Failed to connect transcription client for {speaker_id}")
            return

        # Add to active clients
        async with cleanup_lock:
            active_clients[speaker_id] = ws_client

        # Stream audio with better error handling and resource management
        frames_processed = 0
        bytes_sent = 0
        start_time = time.time()
        last_log_time = start_time
        
        try:
            # Create audio stream with optimized settings
            stream = rtc.AudioStream.from_track(
                track=track, 
                sample_rate=SAMPLE_RATE, 
                num_channels=CHANNELS
            )
            
            async for event in stream:
                # Check if client is still active
                if speaker_id not in active_clients:
                    logger.info(f"Stopping stream for {speaker_id} - client removed")
                    break
                
                # Process audio frame
                frame = event.frame
                audio_data = bytes(frame.data)
                
                # Send audio data (will be batched automatically)
                await ws_client.send_audio(audio_data)
                
                frames_processed += 1
                bytes_sent += len(audio_data)
                
                # Periodic logging
                current_time = time.time()
                if current_time - last_log_time > 30:  # Log every 30 seconds
                    duration = current_time - start_time
                    fps = frames_processed / duration if duration > 0 else 0
                    bps = bytes_sent / duration if duration > 0 else 0
                    
                    logger.info(f"{speaker_id}: {frames_processed} frames, {fps:.1f} FPS, {bps/1024:.1f} KB/s")
                    last_log_time = current_time
            
            logger.info(f"Audio stream ended for {speaker_id} ({frames_processed} frames processed)")
            
        except Exception as e:
            logger.error(f"Error during audio streaming for {speaker_id}: {e}")
        finally:
            # Clean up client
            async with cleanup_lock:
                if speaker_id in active_clients:
                    await ws_client.disconnect()
                    active_clients.pop(speaker_id, None)
                    logger.info(f"Cleaned up transcription for {speaker_id}")

    def on_track_unsubscribed(track: rtc.RemoteTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        """Handle track unsubscription"""
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            pid = participant.identity
            logger.info(f"Audio track unsubscribed for {pid}")
            
            async def cleanup_client():
                async with cleanup_lock:
                    client = active_clients.get(pid)
                    if client:
                        await client.disconnect()
                        active_clients.pop(pid, None)
                        logger.info(f"Cleaned up client for {pid}")
            
            asyncio.create_task(cleanup_client())

    def on_track_subscribed(track: rtc.RemoteAudioTrack, publication: rtc.TrackPublication, participant: rtc.RemoteParticipant):
        """Handle new audio track subscription"""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(f"New audio track from {participant.identity}")
            asyncio.create_task(manage_speaker_transcription(track, publication, participant))

    def on_participant_disconnected(participant: rtc.RemoteParticipant):
        """Handle participant disconnection"""
        pid = participant.identity
        logger.info(f"Participant {pid} disconnected")
        
        async def cleanup_participant():
            async with cleanup_lock:
                client = active_clients.get(pid)
                if client:
                    await client.disconnect()
                    active_clients.pop(pid, None)
                    logger.info(f"Cleaned up disconnected participant {pid}")
        
        asyncio.create_task(cleanup_participant())

    async def on_disconnected():
        """Handle room disconnection"""
        logger.info("Room disconnected, cleaning up all clients")
        await safe_disconnect_all()
        disconnected.set()

    # Set up event handlers
    ctx.room.on("track_subscribed", on_track_subscribed)
    ctx.room.on("track_unsubscribed", on_track_unsubscribed)
    ctx.room.on("participant_disconnected", on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

    # Set agent name
    await ctx.room.local_participant.set_name("Vosk Data Channel Transcription Agent")

    # Send welcome message via data channel
    await transcript_manager.send_welcome_message()

    logger.info("🎤 Vosk Data Channel Agent ready and waiting for participants...")
    
    try:
        await disconnected.wait()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("Shutting down agent...")
        await safe_disconnect_all()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))