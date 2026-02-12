"""
Interview Webhook Service - Send interview track data to configured webhook
"""
import httpx
from typing import Dict, Any, List, Optional
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.mongodb_service import get_mongodb_service

logger = get_logger(__name__)


class InterviewWebhookService:
    """Service to send interview track data to configured webhook"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self.config = get_config()
        self._initialized = True
        logger.info("InterviewWebhookService initialized")
    
    def _get_storage_url(self, location: str) -> str:
        """
        Get storage URL from location field.
        Location is already a full HTTP URL from audio_info.
        
        Args:
            location: Full HTTP URL (e.g., "http://minio:9000/bucket/path/file.ogg")
            
        Returns:
            The location URL as-is
        """
        return location
    
    async def send_interview_data(
        self, 
        interview_id: str, 
        room_id: str
    ) -> bool:
        """
        Send interview track data to configured webhook.
        
        Args:
            interview_id: Interview identifier
            room_id: MongoDB room _id
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.config.interview.enabled:
            logger.info("Interview webhook disabled, skipping")
            return False
        
        if not self.config.interview.webhook_url:
            logger.warning("Interview webhook URL not configured")
            return False
        
        try:
            # Get all tracks for this room
            mongodb = get_mongodb_service()
            tracks = await mongodb.get_tracks_by_room(room_id)
            
            if not tracks:
                logger.warning(f"No tracks found for room_id '{room_id}'")
                return False
            
            # Build tracks dictionary: {started_at_ns: storage_url}
            tracks_data = {}
            for track in tracks:
                audio_info = track.get("audio_info", {})
                
                # Get started_at_ns
                started_at_ns = audio_info.get("started_at_ns")
                if not started_at_ns:
                    logger.warning(f"Track {track.get('_id')} missing started_at_ns, skipping")
                    continue
                
                # Get location (already a full HTTP URL)
                location = audio_info.get("location", "")
                if not location:
                    logger.warning(f"Track {track.get('_id')} missing location, skipping")
                    continue
                
                storage_url = self._get_storage_url(location)
                tracks_data[str(started_at_ns)] = storage_url
            
            if not tracks_data:
                logger.warning(f"No valid tracks with storage URLs found for room_id '{room_id}'")
                return False
            
            # Build request payload
            payload = {
                "interview_id": interview_id,
                "tracks": tracks_data
            }
            
            # Send to webhook
            headers = {
                "Content-Type": "application/json"
            }
            
            # Add API key if configured
            if self.config.interview.webhook_api_key:
                headers["Authorization"] = f"Bearer {self.config.interview.webhook_api_key}"
            
            logger.info(f"📤 Sending interview data to webhook: {self.config.interview.webhook_url}")
            logger.debug(f"Payload: {payload}")
            
            async with httpx.AsyncClient(timeout=self.config.interview.timeout) as client:
                response = await client.post(
                    f"{self.config.interview.webhook_url}/api/interview/audio",
                    json=payload,
                    headers=headers
                )
                
                if response.status_code in (200, 201, 202):
                    logger.info(f"✅ Interview data sent successfully: interview_id={interview_id}")
                    logger.debug(f"Response: {response.text}")
                    return True
                else:
                    logger.error(
                        f"❌ Failed to send interview data: "
                        f"status={response.status_code}, response={response.text}"
                    )
                    return False
        
        except httpx.TimeoutException:
            logger.error(f"⏱️ Timeout sending interview data to webhook")
            return False
        except httpx.RequestError as e:
            logger.error(f"❌ Request error sending interview data: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Unexpected error sending interview data: {e}", exc_info=True)
            return False


def get_interview_webhook_service() -> InterviewWebhookService:
    """Get the singleton interview webhook service instance."""
    return InterviewWebhookService()
