
from typing import Optional, Dict

from livekit import api

from orchestrator_service.utils.filepath import Filepath
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.redis.egress_repository import get_egress_repository, EgressRepository

logger = get_logger(__name__)


class EgressService:
    """LiveKit egress operations management Service"""
    
    def __init__(self):
        self._s3_upload: Optional[api.S3Upload] = None

    def _get_client(self) -> api.LiveKitAPI:
        """Get LiveKit client from centralized service"""
        return get_livekit_service().get_client()
    
    def _get_repo(self, room_name: str) -> EgressRepository:
        """Get egress repository for a room."""
        return get_egress_repository(room_name)
    
    def _get_s3_upload(self) -> api.S3Upload:
        if self._s3_upload is None:
            config = get_config()
            self._s3_upload = api.S3Upload(
                access_key=config.minio.access_key,
                secret=config.minio.secret,
                bucket=config.minio.bucket,
                region=config.minio.region,
                endpoint=config.minio.endpoint,
                force_path_style=True,
            )
        return self._s3_upload
    
    async def start_recording(
        self,
        room_name: str,
        track_sid: str,
        track_type: str,
        source: str,
        identity: str
    ) -> Optional[str]:
        """
        start recording a track
        
        Returns:
            Egress ID if successful, None if failed
        """
        repo = self._get_repo(room_name)
        
        # Check duplicate
        existing_egress_id = await repo.get_egress_id(track_sid)
        if existing_egress_id:
            logger.info(f"⏭ Track {track_sid} was recorded, skipping")
            return existing_egress_id
        
        try:
            lk = self._get_client()
            config = get_config()
            
            # Get room_id from registry
            registry = get_room_registry()
            room_id = await registry.get_room_id(room_name)
            if not room_id:
                logger.error(f"Room '{room_name}' not found in registry")
                raise ValueError(f"Room '{room_name}' not registered")
            

            filepath = Filepath.build(identity, source, track_type, room_id)
            s3_upload = self._get_s3_upload()
            
            file_out = api.DirectFileOutput(filepath=filepath, s3=s3_upload)
            req = api.TrackEgressRequest(
                room_name=room_name,
                track_id=track_sid,
                file=file_out,
            )
            
            result = await lk.egress.start_track_egress(req)
            await repo.add(track_sid, result.egress_id)
            
            logger.info(f"✓ Started egress {result.egress_id}")
            logger.info(f"  MinIO: s3://{config.minio.bucket}/{filepath}")
            
            return result.egress_id
            
        except Exception as e:
            logger.error(f"✗ Failed to start egress: {e}")
            return None
    
    async def stop_recording(self, room_name: str, track_sid: str) -> bool:
        """Stop recording a track"""
        repo = self._get_repo(room_name)
        egress_id = await repo.get_egress_id(track_sid)
        if not egress_id:
            logger.info(f"No active egress for track {track_sid} in room {room_name}")
            return False
        
        try:
            lk = self._get_client()
            
            await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
            await repo.pop(track_sid)
            
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
        repo = self._get_repo(room_name)
        track_sids_to_stop = await repo.get_track_list()
        
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
        
        logger.info(f"Stopped {stopped} egresses for room '{room_name}' ({failed} failed)")
        return {"stopped": stopped, "failed": failed}
    
    async def mark_unpublished(self, room_name: str, track_sid: str) -> bool:
        """Mark track as unpublished (egress auto stopped)"""
        repo = self._get_repo(room_name)
        egress_id = await repo.pop(track_sid)
        return egress_id is not None
    
    async def get_active_count_by_room(self, room_name: str) -> int:
        """Number of active egresses in a room"""
        repo = self._get_repo(room_name)
        return await repo.get_active_count()
    
    async def get_all_active_by_room(self, room_name: str) -> Dict[str, str]:
        """Get all active egresses in a room"""
        repo = self._get_repo(room_name)
        return await repo.get_all_tracks()
    
    async def cleanup_room(self, room_name: str):
        """Cleanup egresses for a specific room"""
        repo = self._get_repo(room_name)
        active_count = await repo.get_active_count()
        if active_count > 0:
            logger.info(f"Stopping {active_count} active egresses in room '{room_name}' before cleanup")
            await self.stop_all_by_room(room_name)
        # Remove the repository instance
        EgressRepository.remove_instance(room_name)

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