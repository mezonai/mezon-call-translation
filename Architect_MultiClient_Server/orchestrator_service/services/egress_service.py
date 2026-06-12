
from typing import Optional, Dict

from livekit import api

from orchestrator_service.models.agent_request_type import AgentRequestType
from orchestrator_service.api.sse.channels.agent_request_channel import AgentRequestChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.utils.filepath import Filepath
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.redis.egress_repository import EgressRepository

logger = get_logger(__name__)


class EgressService:
    """LiveKit egress operations management Service"""
    
    def __init__(self):
        self._s3_upload: Optional[api.S3Upload] = None
        self._repo: Optional[EgressRepository] = None
        self.config = get_config()
    def _get_client(self) -> api.LiveKitAPI:
        """Get LiveKit client from centralized service"""
        return get_livekit_service().get_client()
    
    def _get_repo(self) -> EgressRepository:
        """Get singleton egress repository."""
        if self._repo is None:
            self._repo = EgressRepository()
        return self._repo
    
    def _get_s3_upload(self) -> api.S3Upload:
        if self._s3_upload is None:
            self._s3_upload = api.S3Upload(
                access_key=self.config.minio.access_key,
                secret=self.config.minio.secret,
                bucket=self.config.minio.bucket,
                region=self.config.minio.region,
                endpoint=self.config.minio.endpoint,
                force_path_style=True,
            )
        return self._s3_upload

    def _get_agent_request_channel(self) -> AgentRequestChannel:
        """Get singleton agent request channel with shared SSE manager."""
        return AgentRequestChannel(SSEManager())
    
    async def start_recording(
        self,
        room_name: str,
        track_sid: str,
        track_type: str,
        source: str,
        identity: str,
        force_update: bool = False,
    ) -> Optional[str]:
        """
        start recording a track
        
        Returns:
            Egress ID if successful, None if failed
        """
        repo = self._get_repo()
        
        # Check duplicate
        existing_egress_id = await repo.get_egress_id(room_name, track_sid)
        if existing_egress_id and not force_update:
            logger.info(f"⏭ Track {track_sid} was recorded, skipping")
            return existing_egress_id
        
        try:
            lk = self._get_client()
            
            # Get room_id from registry
            registry = get_room_registry()
            room_id = await registry.get_room_id(room_name)
            if not room_id:
                logger.error(f"Room '{room_name}' not found in registry")
                raise ValueError(f"Room '{room_name}' not registered")
            

            filepath = Filepath.build(identity, source, track_type, room_id)
            s3_upload = self._get_s3_upload()
            try:
                file_out = api.DirectFileOutput(filepath=filepath, s3=s3_upload)
                req = api.TrackEgressRequest(
                    room_name=room_name,
                    track_id=track_sid,
                    file=file_out,
                )
                
                result = await lk.egress.start_track_egress(req)
                await repo.add(room_name, track_sid, result.egress_id)
                
                logger.info(f"✓ Started egress {result.egress_id}")
                logger.info(f"  MinIO: s3://{self.config.minio.bucket}/{filepath}")
                
                return result.egress_id
            except Exception as e:
                logger.error(f"Failed to build egress request: {e}")

                start_recording_payload = {
                    "track_id": track_sid,
                    "file_output_path": filepath
                }

                agent_request_channel = self._get_agent_request_channel()
                result = await agent_request_channel.send_request(
                    request_type=AgentRequestType.START_AUDIO_RECORDING,
                    payload=start_recording_payload,
                    room_name=room_name,
                    agent_id=self.config.livekit.agent_name
                )
                logger.info(
                    f"Fallback START_AUDIO_RECORDING dispatched for track={track_sid}, "
                    f"request_id={result.get('request_id')}"
                )
                return result.get("request_id")

            
        except Exception as e:
            logger.error(f"✗ Failed to start egress: {e}")
            return None
    
    async def stop_recording(self, room_name: str, track_sid: str) -> bool:
        """Stop recording a track"""
        repo = self._get_repo()
        egress_id = await repo.get_egress_id(room_name, track_sid)
        if not egress_id:
            logger.info(f"No active egress for track {track_sid} in room {room_name}")
            return False
        
        try:
            lk = self._get_client()
            
            await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
            await repo.pop(room_name, track_sid)
            
            logger.info(f"✓ Stopped egress {egress_id}")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to stop egress: {e}")
            return False

    async def stop_all_by_room(self, room_name: str) -> Dict[str, int]:
        """
        Stop all active egresses for a specific room.
        
        Args:
            room_name: Name of the room
            
        Returns:
            Dict with counts of stopped and failed egresses
        """
        repo = self._get_repo()
        track_sids_to_stop = await repo.get_track_list(room_name)
        
        if not track_sids_to_stop:
            logger.info(f"No active egresses found for room '{room_name}'")
            return {"stopped": 0, "failed": 0}
        
        logger.info(f"Found {len(track_sids_to_stop)} active egresses for room '{room_name}'")
        
        stopped, failed = 0, 0
        for track_sid in track_sids_to_stop:
            if await self.stop_recording(room_name, track_sid):
                stopped += 1
            else:
                failed += 1
        await repo.stop_all(room_name)
        logger.info(f"Stopped {stopped} egresses for room '{room_name}' ({failed} failed)")
        return {"stopped": stopped, "failed": failed}
    
    async def mark_unpublished(self, room_name: str, track_sid: str) -> bool:
        """Mark track as unpublished (egress auto stopped)"""
        repo = self._get_repo()
        egress_id = await repo.pop(room_name, track_sid)
        return egress_id is not None
    
    async def get_active_count_by_room(self, room_name: str) -> int:
        """Number of active egresses in a room"""
        repo = self._get_repo()
        return await repo.get_active_count(room_name)
    
    async def get_all_active_by_room(self, room_name: str) -> Dict[str, str]:
        """Get all active egresses in a room"""
        repo = self._get_repo()
        return await repo.get_all_tracks(room_name)
    
    async def cleanup_room(self, room_name: str):
        """Cleanup egresses for a specific room"""
        repo = self._get_repo()
        active_count = await repo.get_active_count(room_name)
        if active_count > 0:
            logger.info(f"Stopping {active_count} active egresses in room '{room_name}' before cleanup")
            await self.stop_all_by_room(room_name)

    async def cleanup(self):
        """Cleanup all egresses (used during shutdown)"""
        logger.info("Cleaning up all egresses...")
        # Get list of all rooms from registry
        registry = get_room_registry()
        rooms = await registry.list_rooms()
        
        total_stopped, total_failed = 0, 0
        for room_name in rooms.keys():
            result = await self.stop_all_by_room(room_name)
            total_stopped += result["stopped"]
            total_failed += result["failed"]
        
        logger.info(f"Cleanup completed: Stopped {total_stopped} egresses, {total_failed} failed")