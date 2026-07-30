"""
Event handlers for LiveKit room - manages audio tracks and transcription clients
"""
import asyncio
import json
import time
import numpy as np
from livekit import rtc

from src.config.application_config import get_config
from src.services.orchestrator_client import OrchestratorClient
from src.config import SAMPLE_RATE, CHANNELS
from src.core.websocket.stt_client import STTWebSocketClient
from src.core.transcript_manager import TranscriptManager
from src.logger import get_logger
from src.core.vad_processor import RealTimeVADProcessor
from src.core.agent_control_state import AgentControlState
from src.services.record_service_client import get_record_service_client
from src.utils.participant_identity import parse_participant_identity


logger = get_logger(__name__)

class EventHandlers:
    """Handles room events: track subscription, participant disconnect, audio streaming"""
    
    def __init__(
        self,
        ctx,
        transcript_manager: TranscriptManager,
        control_state: AgentControlState,
        agent_manager=None,
    ):
        self.ctx = ctx
        self.transcript_manager = transcript_manager
        self.control_state = control_state
        self.agent_manager = agent_manager
        self.active_clients = {}  # {participant_id: WebSocketClient}
        self.transcription_tasks = {}  # {speaker_id: asyncio.Task}
        self.pending_tracks = {}  # {speaker_id: (track, publication, participant)}
        self.track_index = {}  # {track_id: (speaker_id, track, publication, participant)}
        self.record_forward_tasks = {}  # {track_id: asyncio.Task} -- record-service forwarding, tied to track/agent lifecycle (not STT toggle), see audio-ingestion/PLAN.md D3
        # Two independent locks, not one shared lock: transcription and
        # record-forwarding are unrelated subsystems with their own dicts
        # (pending_tracks/active_clients/transcription_tasks vs.
        # track_index/record_forward_tasks). Sharing one lock meant a slow
        # operation in either path (e.g. safe_disconnect_all awaiting
        # websocket disconnects) could stall the other's bookkeeping for no
        # reason. See audio-ingestion/PLAN.md D24.
        self._transcription_lock = asyncio.Lock()
        self._record_lock = asyncio.Lock()
        self.logger = get_logger("event_handlers")
        self._orchestrator = OrchestratorClient.get_instance()
        self._record_service_client = get_record_service_client()
        self.config = get_config()

    def session_id_from_room(self) -> str:
        """Generate unique session ID from room metadata"""
        return self.ctx.room.name

    @staticmethod
    def _track_id_from_publication(track: rtc.Track, publication: rtc.TrackPublication) -> str:
        """Best-effort extraction of stable track id for request routing."""
        publication_sid = getattr(publication, "sid", None)
        if publication_sid:
            return publication_sid
        track_sid = getattr(track, "sid", None)
        if track_sid:
            return track_sid
        return ""

    async def _start_record_forwarding(
        self,
        track_id: str,
        track: rtc.RemoteAudioTrack,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        """Start forwarding a track to record-service.

        Tied to track subscription (== agent presence), not to the realtime
        STT enabled toggle: recording feeds the separate non-realtime Whisper
        pipeline and must run whenever the agent is in the room, independent
        of whether manage_speaker_transcription happens to be active for
        this room. See audio-ingestion/PLAN.md D3.
        """
        if not track_id:
            return
        async with self._record_lock:
            if track_id in self.record_forward_tasks:
                return
            task = asyncio.create_task(
                self._forward_track_to_record_service(track_id, track, publication, participant),
                name=f"record-forward-{track_id}",
            )
            self.record_forward_tasks[track_id] = task

    async def _forward_track_to_record_service(
        self,
        track_id: str,
        track: rtc.RemoteAudioTrack,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        """Independent audio subscription feeding record-service. Deliberately
        NOT shared with manage_speaker_transcription's stream -- that one only
        runs when realtime STT is toggled on, and recording must not depend
        on that (audio-ingestion/PLAN.md D3, corrected from the Phase 2 first
        pass which wrongly reused it)."""
        participant_identity = parse_participant_identity(participant.identity)
        source = "screen" if publication.source == 4 else "mic"
        logger.info(f"Starting record-service forwarding for {participant_identity} (track={track_id})")

        forwarder = await self._record_service_client.new_forwarder(
            room_id=self.ctx.room.name,
            track_id=track_id,
            participant_identity=participant_identity,
            source=source,
            sample_rate=SAMPLE_RATE,
            channels=CHANNELS,
        )
        if forwarder is None:
            async with self._record_lock:
                self.record_forward_tasks.pop(track_id, None)
            return

        try:
            stream = rtc.AudioStream.from_track(
                track=track,
                sample_rate=SAMPLE_RATE,
                num_channels=CHANNELS,
            )
            async for event in stream:
                forwarder.send_audio(bytes(event.frame.data))

        except asyncio.CancelledError:
            logger.info(f"Record-service forwarding cancelled for track={track_id}")
            raise
        except Exception as e:
            logger.error(f"Error forwarding track={track_id} to record-service: {e}")
        finally:
            await forwarder.close()
            async with self._record_lock:
                self.record_forward_tasks.pop(track_id, None)
            logger.info(f"Record-service forwarding ended for track={track_id}")

    def create_transcription_callback(self, participant_identity: str):
        """Factory to create callback for processing transcripts from Vosk server"""
        async def transcription_callback(message: str):
            try:
                # Parse JSON hoặc plain text
                data = json.loads(message) if message.startswith('{') else {"text": message}
                text = data.get("text", "").strip()
                
                if text:
                    is_final = bool(data.get("is_final", False))
                    
                    # Create segment with current timestamp
                    receive_time = time.time()
                    segments = [{
                        "text": text,
                        "start": receive_time,
                        "end": receive_time,
                        "completed": bool(data.get("is_final", False))
                    }]
                    
                    # Save transcript via TranscriptManager (log + MongoDB)
                    await self.transcript_manager.send_transcript_entry(
                        text=text,
                        participant_identity=participant_identity,
                        participant_name=participant_identity,
                        is_final=is_final,
                        segments=segments,
                        language=None
                    )
                
            except Exception as e:
                logger.error(f"Error processing transcription for {participant_identity}: {e}")
                
        return transcription_callback

    async def manage_speaker_transcription(
        self, 
        track: rtc.RemoteAudioTrack, 
        publication: rtc.TrackPublication, 
        participant: rtc.RemoteParticipant
    ):
        """
        Core audio processing pipeline:
        LiveKit track -> VAD processor -> WebSocket -> Vosk server
        """
        speaker_id = self._speaker_id_from_publication(participant, publication)

        sid = self.session_id_from_room()
        logger.info(f"Starting transcription for {speaker_id} (session={sid})")

        # WebSocket client connects to Vosk server
        ws_client = STTWebSocketClient(
            client_id=speaker_id,
            session_id=sid,
            transcription_callback=self.create_transcription_callback(speaker_id),
            participant_identity=speaker_id,
        )
        
        # VAD processor: filter silence, batch audio chunks
        processor = RealTimeVADProcessor(
            sr=16000, 
            chunk_duration_ms=10,    # Stream 10ms chunks
            overlap_chunks=2,        # Overlap 20ms để tránh mất âm
            enable_playback=False,
            min_speech_frames=10,    # Cần 100ms liên tiếp mới coi là speech
            save_chunks=False,
            enable_vad=False
        )
        
        processor.start_processing()

        # Kết nối WebSocket trước khi stream audio
        if not await ws_client.connect():
            logger.error(f"Failed to connect transcription client for {speaker_id}")
            return

        async with self._transcription_lock:
            self.active_clients[speaker_id] = ws_client

        # Metrics tracking
        frames_processed = 0
        bytes_sent = 0
        start_time = time.time()
        last_log_time = start_time
        
        try:
            # Audio stream với config tối ưu
            stream = rtc.AudioStream.from_track(
                track=track, 
                sample_rate=SAMPLE_RATE, 
                num_channels=CHANNELS
            )
            
            async for event in stream:
                # Check if client is still active
                if speaker_id not in self.active_clients:
                    logger.info(f"Stopping stream for {speaker_id} - client removed")
                    break
                
                frame = event.frame
                frame_bytes = bytes(frame.data)

                # Convert bytes -> float32 [-1.0, 1.0] cho VAD processing
                audio_data = np.frombuffer(
                    frame_bytes,
                    dtype=np.int16
                ).astype(np.float32) / 32767.0
                
                processor.add_audio_chunk(audio_data)
                
                # Get batched chunks (5 chunks = 50ms) to send to Vosk
                batched_chunks = processor.get_batched_chunks(chunks_per_batch=5)
                for batch in batched_chunks:
                    # Convert back to int16 for sending
                    batch_bytes = (batch * 32767).astype(np.int16).tobytes()
                    await ws_client.send_audio(batch_bytes)
                    frames_processed += 1
                    bytes_sent += len(batch_bytes)
                
                # Log performance mỗi 30s
                current_time = time.time()
                if current_time - last_log_time > 30:
                    duration = current_time - start_time
                    fps = frames_processed / duration if duration > 0 else 0
                    bps = bytes_sent / duration if duration > 0 else 0
                    
                    logger.info(f"{speaker_id}: {frames_processed} frames, {fps:.1f} FPS, {bps/1024:.1f} KB/s")
                    last_log_time = current_time
            
            logger.info(f"Audio stream ended for {speaker_id} ({frames_processed} frames processed)")
            
        except asyncio.CancelledError:
            logger.info(f"Transcription task cancelled for {speaker_id}")
            raise
        except Exception as e:
            logger.error(f"Error during audio streaming for {speaker_id}: {e}")
        finally:
            # Cleanup resources
            try:
                processor.stop_processing()
                logger.info(f"Stopped VAD processing for {speaker_id}")
            except Exception:
                pass

            try:
                await ws_client.disconnect()
            except Exception:
                pass

            async with self._transcription_lock:
                self.active_clients.pop(speaker_id, None)
                self.transcription_tasks.pop(speaker_id, None)

            logger.info(f"Cleaned up transcription for {speaker_id}")

            # Thông báo agent manager về client removal
            if self.agent_manager:
                try:
                    await self.agent_manager.announce_agent_status("client_removed", {
                        "participant_removed": speaker_id,
                        "total_clients": len(self.active_clients),
                        "clients": list(self.active_clients.keys())
                    })
                except Exception:
                    pass

    def _speaker_id_from_publication(self, participant: rtc.RemoteParticipant, publication: rtc.TrackPublication) -> str:
        # Phân biệt audio từ mic vs screen share
        participant_identity = parse_participant_identity(participant.identity)
        if publication.source == 4:  # Screen share audio
            return f"{participant_identity}-screen"
        return participant_identity

    async def _start_transcription_for_speaker_id(self, speaker_id: str) -> bool:
        """Start transcription task for a pending speaker_id if available."""
        async with self._transcription_lock:
            if speaker_id in self.transcription_tasks:
                return False
            pending = self.pending_tracks.get(speaker_id)
            if not pending:
                return False
            track, publication, participant = pending
            task = asyncio.create_task(self.manage_speaker_transcription(track, publication, participant))
            self.transcription_tasks[speaker_id] = task
            return True

    async def start_transcription_for_all_pending(self) -> int:
        """Start transcription for all currently pending tracks (best-effort)."""
        async with self._transcription_lock:
            speaker_ids = list(self.pending_tracks.keys())
        started = 0
        for sid in speaker_ids:
            try:
                if await self._start_transcription_for_speaker_id(sid):
                    started += 1
            except Exception as e:
                logger.error(f"Failed to start transcription for {sid}: {e}")
        return started

    async def stop_transcription_for_all(self) -> int:
        """Stop all active transcription tasks (best-effort)."""
        async with self._transcription_lock:
            tasks = list(self.transcription_tasks.items())
            self.transcription_tasks.clear()
            # Also remove active clients so streams break quickly
            self.active_clients.clear()

        stopped = 0
        for speaker_id, task in tasks:
            try:
                task.cancel()
                stopped += 1
            except Exception:
                pass
        return stopped

    async def get_gate_stats(self) -> dict:
        """Small helper for debug/log/health."""
        async with self._transcription_lock:
            pending = len(self.pending_tracks)
            active = len(self.active_clients)
            running_tasks = len(self.transcription_tasks)
        enabled = await self.control_state.get_transcription_enabled()
        return {
            "transcription_enabled": enabled,
            "pending_tracks": pending,
            "active_clients": active,
            "running_tasks": running_tasks,
        }

    def on_track_subscribed(
        self, 
        track: rtc.RemoteAudioTrack, 
        publication: rtc.TrackPublication, 
        participant: rtc.RemoteParticipant
    ):
        """Handle new audio track subscription"""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            speaker_id = self._speaker_id_from_publication(participant, publication)
            track_id = self._track_id_from_publication(track, publication)
            logger.info(f"New audio track from {speaker_id} (registered; gated)")

            async def register_and_maybe_start():
                async with self._transcription_lock:
                    self.pending_tracks[speaker_id] = (track, publication, participant)
                if track_id:
                    async with self._record_lock:
                        self.track_index[track_id] = (speaker_id, track, publication, participant)

                async def _maybe_start_transcription():
                    try:
                        enabled = await self.control_state.get_transcription_enabled()
                        if enabled:
                            await self._start_transcription_for_speaker_id(speaker_id)
                    except Exception as e:
                        logger.error(f"Failed to auto-start transcription for {speaker_id}: {e}")

                async def _start_recording():
                    try:
                        await self._start_record_forwarding(track_id, track, publication, participant)
                    except Exception as e:
                        logger.error(f"Failed to start record forwarding for track={track_id}: {e}")

                # Run concurrently, not one awaited after the other -- recording
                # follows agent/track lifecycle, not the realtime-STT feature
                # flag, so it must never be delayed by (or skipped because of
                # an exception in) the transcription start path. Each inner
                # coroutine catches its own errors so a failure in one can
                # never prevent the other from running. See PLAN.md D24.
                await asyncio.gather(_maybe_start_transcription(), _start_recording())

            asyncio.create_task(register_and_maybe_start())

    def on_track_unsubscribed(
        self, 
        track: rtc.RemoteTrack, 
        publication: rtc.TrackPublication, 
        participant: rtc.RemoteParticipant
    ):
        """Cleanup khi unsubscribe track"""
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            pid = self._speaker_id_from_publication(participant, publication)
            track_id = self._track_id_from_publication(track, publication)
            logger.info(f"Audio track unsubscribed for {pid}")
            
            async def cleanup_client():
                async with self._transcription_lock:
                    self.pending_tracks.pop(pid, None)
                    client = self.active_clients.pop(pid, None)
                    transcription_task = self.transcription_tasks.pop(pid, None)

                record_forward_task = None
                if track_id:
                    async with self._record_lock:
                        self.track_index.pop(track_id, None)
                        record_forward_task = self.record_forward_tasks.pop(track_id, None)

                if client:
                    await client.disconnect()
                    logger.info(f"Cleaned up client for {pid}")

                if transcription_task:
                    transcription_task.cancel()

                if record_forward_task:
                    record_forward_task.cancel()

                wait_tasks = [
                    task for task in (transcription_task, record_forward_task)
                    if task is not None
                ]
                if wait_tasks:
                    await asyncio.gather(*wait_tasks, return_exceptions=True)

            asyncio.create_task(cleanup_client())

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Cleanup khi participant rời room"""
        participant_identity = participant.identity
        pid = parse_participant_identity(participant_identity)
        logger.info(f"Participant {pid} disconnected")
        
        async def cleanup_participant():
            clients_to_disconnect = []
            transcription_tasks = []
            record_forward_tasks = []

            async with self._transcription_lock:
                # Cleanup pending tracks and tasks for this participant
                self.pending_tracks.pop(pid, None)
                self.pending_tracks.pop(f"{pid}-screen", None)

                for key in (pid, f"{pid}-screen", participant_identity):
                    client = self.active_clients.pop(key, None)
                    if client:
                        clients_to_disconnect.append((key, client))

                    task = self.transcription_tasks.pop(key, None)
                    if task:
                        transcription_tasks.append(task)

            async with self._record_lock:
                for track_id, (_, _, _, track_participant) in list(self.track_index.items()):
                    if getattr(track_participant, "identity", "") == participant_identity:
                        self.track_index.pop(track_id, None)
                        fwd_task = self.record_forward_tasks.pop(track_id, None)
                        if fwd_task:
                            record_forward_tasks.append(fwd_task)

            for key, client in clients_to_disconnect:
                await client.disconnect()
                logger.info(f"Cleaned up disconnected participant client {key}")

            for task in transcription_tasks:
                task.cancel()

            for task in record_forward_tasks:
                task.cancel()

            wait_tasks = transcription_tasks + record_forward_tasks
            if wait_tasks:
                await asyncio.gather(*wait_tasks, return_exceptions=True)

        asyncio.create_task(cleanup_participant())

    async def safe_disconnect_all(self):
        """Disconnect all clients with timeout protection.

        Snapshots + clears each subsystem's state under its own lock first
        (record-forwarding tasks get cancelled immediately), then does the
        slow network waits outside any lock -- record forwarding shutdown no
        longer waits behind the (up to 10s) STT websocket disconnect budget.
        See audio-ingestion/PLAN.md D24.
        """
        async with self._record_lock:
            record_forward_tasks = list(self.record_forward_tasks.values())
            self.record_forward_tasks.clear()
            self.track_index.clear()

        async with self._transcription_lock:
            clients = list(self.active_clients.values())
            transcription_tasks = list(self.transcription_tasks.values())
            self.active_clients.clear()
            self.transcription_tasks.clear()
            self.pending_tracks.clear()

        if not clients and not transcription_tasks and not record_forward_tasks:
            return

        logger.info(f"Disconnecting {len(clients)} active clients")

        for task in record_forward_tasks:
            task.cancel()
        for task in transcription_tasks:
            task.cancel()

        # Create disconnect tasks for all clients, wait with timeout to avoid hanging
        disconnect_tasks = [client.disconnect() for client in clients]
        if disconnect_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*disconnect_tasks, return_exceptions=True),
                    timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.warning("Some clients took too long to disconnect")

        wait_tasks = transcription_tasks + record_forward_tasks
        if wait_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*wait_tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Some background tasks did not stop in time")

        logger.info("All clients disconnected")