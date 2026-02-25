import httpx
from typing import Dict, Optional
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.redis_producer_service import (
    RedisProducerService,
    create_producer_service,
)
from orchestrator_service.models.transcription_task import TranscriptionTask

logger = get_logger(__name__)


class TranscriptionService:
    """Service sent audio to transcription queue via Redis Stream"""
    
    def __init__(self):
        self.config = get_config().stt_service
        self.redis_config = get_config().redis
        self.api_url = f"http://{self.config.host}:{self.config.port}/api/transcribe"
        self.timeout = 30.0
        self._redis_producer: Optional[RedisProducerService[TranscriptionTask]] = None
    
    async def _get_producer(self) -> RedisProducerService[TranscriptionTask]:
        """Get or create Redis producer (lazy initialization)."""
        if not self._redis_producer:
            self._redis_producer = create_producer_service(
                task_class=TranscriptionTask,
                stream_key=None  # Use default from config
            )
            await self._redis_producer.connect()
        return self._redis_producer
    
    async def enqueue(self, egress_info: Dict) -> bool:
        """
        Send egress info directly to Redis Stream.
        
        Args:
            egress_info: Dict with keys: egressId, filename, location, 
                        duration, startedAt, endedAt, source (optional)
        
        Returns:
            True if successful, False if failed
        """
        try:
            producer = await self._get_producer()
            
            # Create task object
            task = TranscriptionTask(
                egress_id=egress_info["egressId"],
                filename=egress_info["filename"],
                location=egress_info["location"],
                duration=egress_info["duration"],
                started_at=egress_info["startedAt"],
                ended_at=egress_info["endedAt"],
                source=egress_info.get("source", ""),
            )
            
            task_id = await producer.enqueue(task)
            
            logger.info(f"✓ Queued to Redis: {egress_info['egressId']} → {task_id}")
            return True
            
        except Exception as e:
            logger.error(f"✗ Redis enqueue failed: {e}")
            return False

    async def final_room(self, room_name: str, room_id: str ) -> bool:
        """
        Mark room as finalized in transcription service
        
        Args:
            room_name: Name of the room
            room_id: Room ID
            
        Returns:
            True if successful
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"📤 Finalizing room: {room_name}")
                
                response = await client.put(
                    f"{self.api_url}/rooms/end",
                    json={"name": room_name, "room_id": room_id},
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    logger.info(f"✓ Room finalized: {room_name}")
                    return True
                else:
                    logger.error(f"✗ Room finalization failed: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"✗ Error finalizing room: {e}")
            return False

    async def save_track_metadata(
        self, 
        egress_id: str, 
        track_id: str, 
        room_ref_id: str, 
        participant_identity: str, 
    ) -> bool:
        """
        Save track metadata to STT service
        
        Args:
            egress_id: Egress ID (used as track _id)
            track_id: Track ID
            room_ref_id: Room reference ID (ObjectId string)
            participant_identity: Participant identity
            
        Returns:
            True if successful
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"📤 Saving track metadata: egress={egress_id}")
                
                payload = {
                    "egress_id": egress_id,
                    "track_id": track_id,
                    "room_ref_id": room_ref_id,
                    "participant_identity": participant_identity,
                }
                
                response = await client.post(
                    f"{self.api_url}/tracks/metadata",
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                
                if response.status_code == 200:
                    logger.info(f"✓ Track metadata saved: {egress_id}")
                    logger.debug(f"Response: {response.json()}")
                    return True
                else:
                    logger.error(f"✗ Save track metadata failed: {response.status_code}")
                    logger.error(f"Response: {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"✗ Error saving track metadata: {e}")
            return False
        

    async def start_room(self, room_name: str) -> Optional[dict]:
        """
        Notify transcription service to start room

        Returns:
            dict response if success, None if failed
        """
        url = f"{self.api_url}/rooms/start"
        payload = {"room_name": room_name}

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                logger.info(f"📤 Starting room: {room_name} -> {url}")

                response = await client.post(
                    url,
                    json=payload,
                )

                response.raise_for_status()

                data = response.json()

                logger.info(f"✓ Room started: {room_name}")
                logger.debug(f"Response: {data}")

                return data

        except httpx.TimeoutException:
            logger.error("✗ Timeout while starting room")

        except httpx.HTTPStatusError as e:
            logger.error(f"✗ HTTP error: {e.response.status_code} - {e.response.text}")

        except Exception as e:
            logger.exception(f"✗ Unexpected error starting room: {e}")

        return None