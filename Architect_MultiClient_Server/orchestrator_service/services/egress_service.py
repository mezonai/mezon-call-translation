
from typing import Optional, Dict
from datetime import datetime

from livekit import api
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.livekit_client import get_livekit_service

logger = get_logger(__name__)


class EgressService:
    """LiveKit egress operations management Service"""
    
    def __init__(self):
        self.active_egresses: Dict[str, str] = {}
        self._s3_upload: Optional[api.S3Upload] = None

    def _get_client(self) -> api.LiveKitAPI:
        """Get LiveKit client from centralized service"""
        return get_livekit_service().get_client()
    
    def _build_filepath(self, room_name: str, identity: str, source: str, 
                       track_type: str) -> str:
        """Create filepath for MinIO storage"""
        ext = "ogg" if track_type == "AUDIO" else "webm"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{room_name}/{identity}-{source}-{track_type.lower()}-{timestamp}.{ext}"
    
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
        if track_sid in self.active_egresses:
            logger.info(f"⏭ Track {track_sid} was recorded, skipping")
            return self.active_egresses[track_sid]
        
        try:
            lk = self._get_client()
            config = get_config()
            
            filepath = self._build_filepath(room_name, identity, source, track_type)
            s3_upload = self._get_s3_upload()
            
            file_out = api.DirectFileOutput(filepath=filepath, s3=s3_upload)
            req = api.TrackEgressRequest(
                room_name=room_name,
                track_id=track_sid,
                file=file_out,
            )
            
            result = await lk.egress.start_track_egress(req)
            self.active_egresses[track_sid] = result.egress_id
            
            logger.info(f"✓ Started egress {result.egress_id}")
            logger.info(f"  MinIO: s3://{config.minio.bucket}/{filepath}")
            
            return result.egress_id
            
        except Exception as e:
            logger.error(f"✗ Failed to start egress: {e}")
            return None
    
    async def stop_recording(self, track_sid: str) -> bool:
        """Stop recording a track"""
        if track_sid not in self.active_egresses:
            logger.info(f"No active egress for track {track_sid}")
            return False
        
        try:
            lk = self._get_client()
            egress_id = self.active_egresses[track_sid]
            
            await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
            del self.active_egresses[track_sid]
            
            logger.info(f"✓ Stopped egress {egress_id}")
            return True
            
        except Exception as e:
            logger.error(f"✗ Failed to stop egress: {e}")
            return False
    
    async def stop_all(self) -> Dict[str, int]:
        """Stop all active egresses"""
        if not self.active_egresses:
            return {"stopped": 0, "failed": 0}
        
        stopped, failed = 0, 0
        track_sids = list(self.active_egresses.keys())
        
        for track_sid in track_sids:
            if await self.stop_recording(track_sid):
                stopped += 1
            else:
                failed += 1
        
        return {"stopped": stopped, "failed": failed}
    
    def mark_unpublished(self, track_sid: str) -> bool:
        """Mark track as unpublished (egress auto stopped)"""
        if track_sid in self.active_egresses:
            del self.active_egresses[track_sid]
            return True
        return False
    
    def get_active_count(self) -> int:
        """Number of active egresses"""
        return len(self.active_egresses)
    
    def get_all_active(self) -> Dict[str, str]:
        """Get list of all active egresses"""
        return self.active_egresses.copy()
    
    async def cleanup(self):
        """Cleanup resources (client managed by LiveKitClientService)"""
        # Stop all active egresses before cleanup
        if self.active_egresses:
            logger.info(f"Stopping {len(self.active_egresses)} active egresses before cleanup")
            await self.stop_all()
        # Note: LiveKit client cleanup is handled by LiveKitClientService