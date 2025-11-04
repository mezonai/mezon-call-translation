"""
Event handlers cho LiveKit room - Quản lý audio tracks và transcription clients
"""
import asyncio
import json
import time
import numpy as np
from livekit import rtc

from src.config import SAMPLE_RATE, CHANNELS
from src.core.websocket_client import WebSocketTranscriptionClient
from src.core.transcript_manager import TranscriptManager
from src.logger import get_logger
from src.core.vad_processor import RealTimeVADProcessor

logger = get_logger(__name__)


class EventHandlers:
    """Xử lý room events: track subscription, participant disconnect, audio streaming"""
    
    def __init__(self, ctx, transcript_manager: TranscriptManager, agent_manager=None):
        self.ctx = ctx
        self.transcript_manager = transcript_manager
        self.agent_manager = agent_manager
        self.active_clients = {}  # {participant_id: WebSocketClient}
        self.cleanup_lock = asyncio.Lock()
        self.logger = get_logger("event_handlers")

    def session_id_from_room(self) -> str:
        """Generate unique session ID from room metadata"""
        return self.ctx.room.name or self.ctx.room.sid or f"room_{int(time.time())}"

    def create_transcription_callback(self, participant_identity: str):
        """Factory tạo callback xử lý transcript từ Vosk server"""
        async def transcription_callback(message: str):
            try:
                # Parse JSON hoặc plain text
                data = json.loads(message) if message.startswith('{') else {"text": message}
                text = data.get("text", "").strip()
                
                if text:
                    is_final = bool(data.get("is_final", False))
                    
                    # Tạo segment với timestamp hiện tại
                    receive_time = time.time()
                    segments = [{
                        "text": text,
                        "start": receive_time,
                        "end": receive_time,
                        "completed": bool(data.get("is_final", False))
                    }]
                    
                    # Lưu transcript qua TranscriptManager (log + MongoDB)
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
        # Phân biệt audio từ mic vs screen share
        if publication.source == 4:  # Screen share audio
            speaker_id = f"{participant.identity}-screen"
        else:
            speaker_id = participant.identity

        sid = self.session_id_from_room()
        logger.info(f"Starting transcription for {speaker_id} (session={sid})")

        # WebSocket client kết nối Vosk server
        ws_client = WebSocketTranscriptionClient(
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
            enable_vad=True
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
                # Check client còn active không
                if speaker_id not in self.active_clients:
                    logger.info(f"Stopping stream for {speaker_id} - client removed")
                    break
                
                frame = event.frame
                
                # Convert bytes -> float32 [-1.0, 1.0] cho VAD processing
                audio_data = np.frombuffer(
                    bytes(frame.data), 
                    dtype=np.int16
                ).astype(np.float32) / 32767.0
                
                processor.add_audio_chunk(audio_data)
                
                # Lấy batched chunks (5 chunks = 50ms) để gửi Vosk
                batched_chunks = processor.get_batched_chunks(chunks_per_batch=5)
                for batch in batched_chunks:
                    # Convert lại sang int16 để gửi
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
            
        except Exception as e:
            logger.error(f"Error during audio streaming for {speaker_id}: {e}")
        finally:
            # Cleanup resources
            async with self.cleanup_lock:
                if speaker_id in self.active_clients:
                    processor.stop_processing()
                    logger.info(f"Stopped VAD processing for {speaker_id}")
                    
                    await ws_client.disconnect()
                    self.active_clients.pop(speaker_id, None)
                    logger.info(f"Cleaned up transcription for {speaker_id}")
                    
                    # Thông báo agent manager về client removal
                    if self.agent_manager:
                        await self.agent_manager.announce_agent_status("client_removed", {
                            "participant_removed": speaker_id,
                            "total_clients": len(self.active_clients),
                            "clients": list(self.active_clients.keys())
                        })

    def on_track_subscribed(
        self, 
        track: rtc.RemoteAudioTrack, 
        publication: rtc.TrackPublication, 
        participant: rtc.RemoteParticipant
    ):
        """Xử lý khi subscribe audio track mới"""
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(f"New audio track from {participant.identity}")
            asyncio.create_task(
                self.manage_speaker_transcription(track, publication, participant)
            )

    def on_track_unsubscribed(
        self, 
        track: rtc.RemoteTrack, 
        publication: rtc.TrackPublication, 
        participant: rtc.RemoteParticipant
    ):
        """Cleanup khi unsubscribe track"""
        if getattr(track, "kind", None) == rtc.TrackKind.KIND_AUDIO:
            # Xử lý screen share audio riêng
            if publication.source == 4:
                pid = f"{participant.identity}-screen"
            else:
                pid = participant.identity

            logger.info(f"Audio track unsubscribed for {pid}")
            
            async def cleanup_client():
                async with self.cleanup_lock:
                    client = self.active_clients.get(pid)
                    if client:
                        await client.disconnect()
                        self.active_clients.pop(pid, None)
                        logger.info(f"Cleaned up client for {pid}")
            
            asyncio.create_task(cleanup_client())

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Cleanup khi participant rời room"""
        pid = participant.identity
        logger.info(f"Participant {pid} disconnected")
        
        async def cleanup_participant():
            async with self.cleanup_lock:
                client = self.active_clients.get(pid)
                if client:
                    await client.disconnect()
                    self.active_clients.pop(pid, None)
                    logger.info(f"Cleaned up disconnected participant {pid}")
        
        asyncio.create_task(cleanup_participant())

    async def safe_disconnect_all(self):
        """Disconnect tất cả clients với timeout protection"""
        async with self.cleanup_lock:
            if not self.active_clients:
                return
                
            logger.info(f"Disconnecting {len(self.active_clients)} active clients")
            
            # Tạo disconnect tasks cho tất cả clients
            tasks = [
                client.disconnect() 
                for client in list(self.active_clients.values())
            ]
            
            # Wait với timeout để tránh hang
            if tasks:
                try:
                    await asyncio.wait_for(
                        asyncio.gather(*tasks, return_exceptions=True), 
                        timeout=10.0
                    )
                except asyncio.TimeoutError:
                    logger.warning("Some clients took too long to disconnect")
                    
            self.active_clients.clear()
            logger.info("All clients disconnected")