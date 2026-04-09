"""
Event handlers for LiveKit room - manages audio tracks and transcription clients
"""
import asyncio
import json
import time
import numpy as np
from livekit import rtc

from src.config.application_config import get_config
from src.utils.generate_id import generate_id
from src.services.orchestrator_client import EgressInfo, EgressWebhook, FileConfig, FileResult, OrchestratorClient, TrackInfo
from src.config import SAMPLE_RATE, CHANNELS
from src.core.websocket.stt_client import STTWebSocketClient
from src.core.transcript_manager import TranscriptManager
from src.logger import get_logger
from src.core.vad_processor import RealTimeVADProcessor
from src.core.agent_control_state import AgentControlState
from src.services.audio_recording_manager import AudioRecordingManager
from src.utils.participant_identity import parse_participant_identity


logger = get_logger(__name__)

class EventHandlers:
    """Handles room events: track subscription, participant disconnect, audio streaming"""
    
    def __init__(
        self,
        ctx,
        transcript_manager: TranscriptManager,
        control_state: AgentControlState,
        recording_manager: AudioRecordingManager,
        agent_manager=None,
    ):
        self.ctx = ctx
        self.transcript_manager = transcript_manager
        self.control_state = control_state
        self.recording_manager = recording_manager
        self.agent_manager = agent_manager
        self.active_clients = {}  # {participant_id: WebSocketClient}
        self.transcription_tasks = {}  # {speaker_id: asyncio.Task}
        self.pending_tracks = {}  # {speaker_id: (track, publication, participant)}
        self.track_index = {}  # {track_id: (speaker_id, track, publication, participant)}
        self.recording_tasks = {}  # {track_id: asyncio.Task}
        self._egress_sessions = {}  # {track_id: {"egress_id": str, "room_id": str, "room_name": str, "track_id": str, "filepath": str, "start_ns": int}}
        self.recording_metadata = {}  # {track_id: {"egress_id": str, "started_at": datetime, ...}}
        self.cleanup_lock = asyncio.Lock()
        self.logger = get_logger("event_handlers")
        self._orchestrator = OrchestratorClient.get_instance()
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

    async def has_track(self, track_id: str) -> bool:
        """Check whether a track is currently known by the agent."""
        async with self.cleanup_lock:
            return track_id in self.track_index

    async def _handle_egress_start(self, track_id: str, file_output_path: str):
        now = time.time_ns()
        room_id = await self.ctx.room.sid
        room_name = self.ctx.room.name
        track_id = track_id

        egress_id = generate_id("EG_", 12)

        self._egress_sessions[track_id] = {
            "egress_id": egress_id,
            "room_id": room_id,
            "room_name": room_name,
            "track_id": track_id,
            "filepath": file_output_path,
            "start_ns": now,
        }

        file = FileConfig(
            filepath=file_output_path,
            s3={
                "accessKey": "{access_key}",
                "secret": "{secret}",
                "region": self.config.minio.region,
                "endpoint": self.config.minio.endpoint,
                "bucket": self.config.minio.bucket,
                "forcePathStyle": self.config.minio.force_path_style,
            }
        )

        track_info_obj = TrackInfo(
            roomName=room_name,
            trackId=track_id,
            file=file
        )

        egress_info_obj = EgressInfo(
            egressId=egress_id,
            roomId=room_id,
            roomName=room_name,
            startedAt=str(now),
            updatedAt=str(now),
            track=track_info_obj,
            file=None,            
            fileResults=[]
        )

        webhook = EgressWebhook(
            event="egress_started",
            egressInfo=egress_info_obj
        )

        await self._orchestrator.push_webhook(webhook)

    async def _handle_egress_end(self, track_id: str):
        session = self._egress_sessions.get(track_id)

        if not session:
            # Session might have been finalized by another concurrent cleanup path.
            self.logger.info(f"Egress session already finalized or missing for track {track_id}")
            return False

        end_ns = time.time_ns()
        start_ns = session["start_ns"]
        duration = end_ns - start_ns

        filepath = session["filepath"]

        location = f"{self.config.minio.endpoint}/{self.config.minio.bucket}/{filepath}"

        file_result = FileResult(
            filename=filepath,
            startedAt=str(start_ns),
            endedAt=str(end_ns),
            duration=str(duration),
            size=None,
            location=location
        )

        track_info_obj = TrackInfo(
            roomName=session["room_name"],
            trackId=session["track_id"],
            file=FileConfig(
                filepath=filepath,
                s3={
                    "accessKey": "{access_key}",
                    "secret": "{secret}",
                    "region": self.config.minio.region,
                    "endpoint": self.config.minio.endpoint,
                    "bucket": self.config.minio.bucket,
                    "forcePathStyle": self.config.minio.force_path_style,
                }
            )
        )

        egress_info_obj = EgressInfo(
            egressId=session["egress_id"],
            roomId=session["room_id"],
            roomName=session["room_name"],
            startedAt=str(start_ns),
            endedAt=str(end_ns),
            updatedAt=str(end_ns),
            status="EGRESS_COMPLETE",
            details="End reason: Source closed",
            track=track_info_obj,
            file=file_result,
            fileResults=[file_result]
        )

        webhook = EgressWebhook(
            event="egress_ended",
            egressInfo=egress_info_obj
        )

        await self._orchestrator.push_webhook(webhook)

        del self._egress_sessions[track_id]

        return True


    async def start_audio_recording(self, track_id: str, file_output_path: str) -> bool:
        """Start track recording and ensure there is an audio source feeding uploader."""
        async with self.cleanup_lock:
            track_context = self.track_index.get(track_id)

        if not track_context:
            logger.warning(f"Track '{track_id}' not found, cannot start recording")
            return False

        started = await self.recording_manager.start_recording(track_id, file_output_path)
        if not started:
            return False

        speaker_id, track, publication, participant = track_context

        async with self.cleanup_lock:
            has_recording_task = track_id in self.recording_tasks

            if has_recording_task:
                return True
            
            task = asyncio.create_task(
                self._manage_track_recording(track_id, track, publication, participant),
                name=f"recording-{track_id}",
            )
            await self._handle_egress_start(track_id, file_output_path)
            self.recording_tasks[track_id] = task

        logger.info(f"Started dedicated recording stream task for track={track_id}")
        return True

    async def _manage_track_recording(
        self,
        track_id: str,
        track: rtc.RemoteAudioTrack,
        publication: rtc.TrackPublication,
        participant: rtc.RemoteParticipant,
    ):
        """Recording-only stream path used when transcription task is not active."""
        speaker_id = self._speaker_id_from_publication(participant, publication)
        logger.info(f"Starting recording-only stream for {speaker_id} (track={track_id})")

        try:
            stream = rtc.AudioStream.from_track(
                track=track,
                sample_rate=SAMPLE_RATE,
                num_channels=CHANNELS,
            )

            async for event in stream:
                if not await self.recording_manager.has_active_recording(track_id):
                    break
                await self.recording_manager.append_audio(track_id, bytes(event.frame.data))

        except asyncio.CancelledError:
            logger.info(f"Recording-only task cancelled for track={track_id}")
            raise
        except Exception as e:
            logger.error(f"Error in recording-only stream for track={track_id}: {e}")
        finally:
            await self.recording_manager.stop_recording(track_id)
            async with self.cleanup_lock:
                await self._handle_egress_end(track_id)
                self.recording_tasks.pop(track_id, None)
            logger.info(f"Recording-only stream ended for track={track_id}")

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

        async with self.cleanup_lock:
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

            async with self.cleanup_lock:
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
        async with self.cleanup_lock:
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
        async with self.cleanup_lock:
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
        async with self.cleanup_lock:
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
        async with self.cleanup_lock:
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
                async with self.cleanup_lock:
                    self.pending_tracks[speaker_id] = (track, publication, participant)
                    if track_id:
                        self.track_index[track_id] = (speaker_id, track, publication, participant)
                enabled = await self.control_state.get_transcription_enabled()
                if enabled:
                    await self._start_transcription_for_speaker_id(speaker_id)

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
                client = None
                transcription_task = None
                recording_task = None
                should_finalize_egress = False

                async with self.cleanup_lock:
                    self.pending_tracks.pop(pid, None)
                    client = self.active_clients.pop(pid, None)
                    transcription_task = self.transcription_tasks.pop(pid, None)
                    if track_id:
                        self.track_index.pop(track_id, None)
                        recording_task = self.recording_tasks.pop(track_id, None)
                        should_finalize_egress = recording_task is None

                if client:
                    await client.disconnect()
                    logger.info(f"Cleaned up client for {pid}")

                if transcription_task:
                    transcription_task.cancel()

                if recording_task:
                    recording_task.cancel()

                wait_tasks = [
                    task for task in (transcription_task, recording_task)
                    if task is not None
                ]
                if wait_tasks:
                    await asyncio.gather(*wait_tasks, return_exceptions=True)

                if track_id and should_finalize_egress:
                    await self._handle_egress_end(track_id)
            
            asyncio.create_task(cleanup_client())

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Cleanup khi participant rời room"""
        participant_identity = participant.identity
        pid = parse_participant_identity(participant_identity)
        logger.info(f"Participant {pid} disconnected")
        
        async def cleanup_participant():
            clients_to_disconnect = []
            transcription_tasks = []
            recording_tasks = []
            tracks_to_finalize = []

            async with self.cleanup_lock:
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

                for track_id, (_, _, _, track_participant) in list(self.track_index.items()):
                    if getattr(track_participant, "identity", "") == participant_identity:
                        self.track_index.pop(track_id, None)
                        rec_task = self.recording_tasks.pop(track_id, None)
                        if rec_task:
                            recording_tasks.append(rec_task)
                        else:
                            tracks_to_finalize.append(track_id)

            for key, client in clients_to_disconnect:
                await client.disconnect()
                logger.info(f"Cleaned up disconnected participant client {key}")

            for task in transcription_tasks:
                task.cancel()

            for task in recording_tasks:
                task.cancel()

            wait_tasks = transcription_tasks + recording_tasks
            if wait_tasks:
                await asyncio.gather(*wait_tasks, return_exceptions=True)

            for track_id in tracks_to_finalize:
                await self._handle_egress_end(track_id)
        
        asyncio.create_task(cleanup_participant())

    async def safe_disconnect_all(self):
        """Disconnect all clients with timeout protection"""
        transcription_tasks = []
        recording_tasks = []

        async with self.cleanup_lock:
            if not self.active_clients and not self.recording_tasks and not self.transcription_tasks:
                return
                
            logger.info(f"Disconnecting {len(self.active_clients)} active clients")
            
            # Create disconnect tasks for all clients
            tasks = [
                client.disconnect() 
                for client in list(self.active_clients.values())
            ]
            
            # Wait with timeout to avoid hanging
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), 
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Some clients took too long to disconnect")

            transcription_tasks = list(self.transcription_tasks.values())
            recording_tasks = list(self.recording_tasks.values())

            for task in transcription_tasks:
                task.cancel()

            for task in recording_tasks:
                task.cancel()

            self.active_clients.clear()
            self.transcription_tasks.clear()
            self.recording_tasks.clear()

        wait_tasks = transcription_tasks + recording_tasks
        if wait_tasks:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*wait_tasks, return_exceptions=True),
                    timeout=10.0,
                )
            except asyncio.TimeoutError:
                logger.warning("Some background tasks did not stop in time")

        await self.recording_manager.stop_all_recordings()
        logger.info("All clients disconnected")