
from typing import Optional, Dict
from datetime import datetime

from livekit import api
from orchestrator_service.utils.filepath import Filepath
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.redis.active_egress_repository import get_active_egress_repository

logger = get_logger(__name__)


class EgressService:
    """LiveKit egress operations management Service"""
    
    def __init__(self):
        self._active_egress_repo = get_active_egress_repository()
        self.egress_rooms: Dict[str, str] = {}  # {track_sid: room_name}
        self._s3_upload: Optional[api.S3Upload] = None

    def _get_client(self) -> api.LiveKitAPI:
        """Get LiveKit client from centralized service"""
        return get_livekit_service().get_client()
    
    
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
        # Check duplicate
        existing_egress_id = await self._active_egress_repo.get_egress_id(track_sid)
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
            await self._active_egress_repo.add(track_sid, result.egress_id)
            self.egress_rooms[track_sid] = room_name  # Track which room this egress belongs to
            
            logger.info(f"✓ Started egress {result.egress_id}")
            logger.info(f"  MinIO: s3://{config.minio.bucket}/{filepath}")
            
            return result.egress_id
            
        except Exception as e:
            logger.error(f"✗ Failed to start egress: {e}")
            return None
    
    async def stop_recording(self, track_sid: str) -> bool:
        """Stop recording a track"""
        egress_id = await self._active_egress_repo.get_egress_id(track_sid)
        if not egress_id:
            logger.info(f"No active egress for track {track_sid}")
            return False
        
        try:
            lk = self._get_client()
            
            await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
            self.egress_rooms.pop(track_sid, None)  # Remove room mapping
            await self._active_egress_repo.pop(track_sid)
            
            logger.info(f"✓ Stopped egress {egress_id}")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to stop egress: {e}")
            return False
    
    async def stop_all(self) -> Dict[str, int]:
        """Stop all active egresses"""
        active_egresses = await self._active_egress_repo.get_all_active_egresses()
        if not active_egresses:
            return {"stopped": 0, "failed": 0}
        
        stopped, failed = 0, 0
        track_sids = list(active_egresses.keys())
        
        for track_sid in track_sids:
            if await self.stop_recording(track_sid):
                stopped += 1
            else:
                failed += 1
        
        return {"stopped": stopped, "failed": failed}
    
    async def stop_all_by_room(self, room_name: str) -> Dict[str, int]:
        """
        Stop all active egresses for a specific room.
        
        Args:
            room_name: Name of the room
            
        Returns:
            Dict with counts of stopped and failed egresses
        """
        active_egresses = await self._active_egress_repo.get_all_active_egresses()
        if not active_egresses:
            return {"stopped": 0, "failed": 0}
        
        stopped, failed = 0, 0
        # Find all track_sids that belong to this room
        track_sids_to_stop = [
            track_sid for track_sid, room in self.egress_rooms.items()
            if room == room_name
        ]
        
        if not track_sids_to_stop:
            logger.info(f"No active egresses found for room '{room_name}'")
            return {"stopped": 0, "failed": 0}
        logger.info(f"Found {len(track_sids_to_stop)} active egresses for room '{room_name}'")
        logger.info(f"Stopping {len(track_sids_to_stop)} egresses for room '{room_name}'")
        
        # Stop each egress
        for track_sid in track_sids_to_stop:
            if await self.stop_recording(track_sid):
                stopped += 1
            else:
                failed += 1
        
        logger.info(f"Stopped {stopped} egresses for room '{room_name}' ({failed} failed)")
        return {"stopped": stopped, "failed": failed}
    
    async def mark_unpublished(self, track_sid: str) -> bool:
        """Mark track as unpublished (egress auto stopped)"""
        egress_id = await self._active_egress_repo.pop(track_sid)
        if egress_id:
            self.egress_rooms.pop(track_sid, None)  # Remove room mapping
            return True
        return False
    
    async def get_active_count(self) -> int:
        """Number of active egresses"""
        return await self._active_egress_repo.get_active_count()
    
    async def get_all_active(self) -> Dict[str, str]:
        """Get list of all active egresses"""
        return await self._active_egress_repo.get_all_active_egresses()
    
    async def cleanup(self):
        """Cleanup resources (client managed by LiveKitClientService)"""
        # Stop all active egresses before cleanup
        active_count = await self._active_egress_repo.get_active_count()
        if active_count > 0:
            logger.info(f"Stopping {active_count} active egresses before cleanup")
            await self.stop_all()
        # Note: LiveKit client cleanup is handled by LiveKitClientService