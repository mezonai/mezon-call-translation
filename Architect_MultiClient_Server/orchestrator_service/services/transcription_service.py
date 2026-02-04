import httpx
from typing import Dict
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)


class TranscriptionService:
    """Service sent audio to transcription queue"""
    
    def __init__(self):
        self.config = get_config().stt_service
        self.api_url =(f"http://{self.config.host}:{self.config.port}/api/transcribe")
        self.timeout = 30.0
    
    async def enqueue(self, egress_info: Dict) -> bool:
        """
        Send egress info to transcription queue
        
        Returns:
            True if successful, False if failed
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"📤 Sending to transcribe: {self.api_url}")
                
                response = await client.post(
                    f"{self.api_url}/queue",
                    json=egress_info,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    logger.info(f"✓ Queued: {egress_info['egressId']}")
                    logger.debug(f"Response: {response.json()}")
                    return True
                else:
                    logger.error(f"✗ Queue failed. Status: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return False
                    
        except httpx.TimeoutException:
            logger.error("✗ Timeout sending to transcribe queue")
            return False
        except Exception as e:
            logger.error(f"✗ Error sending to queue: {e}")
            return False

    async def final_room(self, room_name: str) -> bool:
        """
        Notify transcription service to finalize room
        
        Returns:
            True if successful, False if failed
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                url = f"{self.api_url}/rooms/{room_name}/end"
                logger.info(f"📤 Notifying final room: {url}")
                
                response = await client.put(
                    url,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    logger.info(f"✓ Finalized room: {room_name}")
                    logger.debug(f"Response: {response.json()}")
                    return True
                else:
                    logger.error(f"✗ Finalize room failed. Status: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return False
                    
        except httpx.TimeoutException:
            logger.error("✗ Timeout notifying final room")
            return False
        except Exception as e:
            logger.error(f"✗ Error notifying final room: {e}")
            return False