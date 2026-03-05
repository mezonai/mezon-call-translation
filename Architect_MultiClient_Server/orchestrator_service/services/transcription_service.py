
from typing import Dict
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.mongodb_service import MongoDBService
from typing import Optional
from bson import ObjectId
from orchestrator_service.api.sse_metadata_api import metadata_channel
from orchestrator_service.services.summary_service import get_summary_service
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
        self.mongodb_service = MongoDBService()
        self._redis_producer: Optional[RedisProducerService[TranscriptionTask]] = None
        self.stream_key = "transcription:stream"
    async def _get_producer(self) -> RedisProducerService[TranscriptionTask]:
        """Get or create Redis producer (lazy initialization)."""
        if not self._redis_producer:
            self._redis_producer = create_producer_service(
                task_class=TranscriptionTask,
                stream_key=self.stream_key,
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
            try:
                if not self.mongodb_service.connected:
                    await self.mongodb_service.connect()
                track_result = await self.mongodb_service.save_track_metadata(            
                    egress_id=egress_info.get("egressId"),
                    audio_info={
                        "filename": egress_info.get("filename"),
                        "duration_sec": egress_info.get("duration"),
                        "started_at_ns": egress_info.get("startedAt"),
                        "ended_at_ns": egress_info.get("endedAt"),
                        "location": egress_info.get("location"),
                        "source": egress_info.get("source")
                    },
                    status="wait_process")
                
                if track_result and track_result.get("room_ref_id"):
                    room = await self.mongodb_service.check_event_record_done(str(track_result.get("room_ref_id")))
                    if room:
                        await metadata_channel.push_room_record_done(
                        room_id=str(room.get("_id")),
                        room_name=room.get("room_name")
                    )
                elif track_result:
                    logger.warning(f"Track metadata saved but no room_ref_id found: {track_result}")
                else:
                    logger.warning("Failed to save track metadata: track_result is None")

                logger.info(f"✅ Track metadata updated: egress={egress_info.get('egressId')}")
            except Exception as e:
                logger.warning(f"Failed to update track metadata: {e}")
                # Continue processing even if metadata update fails

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
            if not self.mongodb_service.connected:
                await self.mongodb_service.connect()
            updated = await self.mongodb_service.final_room_status(
                room_name=room_name,
                room_id=room_id
            )

            if not updated:
                return False
            
            if await self.mongodb_service.check_event_record_done(room_id):
                await metadata_channel.push_room_record_done(
                    room_id=room_id,
                    room_name=room_name
                )

            if await self.mongodb_service.check_and_complete_room(room_id):
                service = get_summary_service()
                await service.generate_summary(room_id)



                await metadata_channel.push_room_summary_done(
                    room_id=room_id,
                    room_name=room_name
                )

            return True
        except Exception as e:
            logger.exception("Failed to end room transcription: %s", e)
            return False



    async def start_room(self, room_name: str) -> Optional[dict]:
        """
        Notify transcription service to start room

        Returns:
            dict response if success, None if failed
        """
        try:
            if not self.mongodb_service.connected:
                await self.mongodb_service.connect()
            room_id =  await self.mongodb_service.create_room_session(
                room_name=room_name
            )
            return {
                "success": True,
                "message": f"Room {room_name} started successfully",
                "room_id": str(room_id)
            }
        except Exception as e:
            logger.exception(f"✗ Unexpected error starting room: {e}")

        return None
    

    async def save_track_metadata(
        self, 
        egress_id: str,
        track_id: str,
        room_ref_id: ObjectId,
        participant_identity: str,
        status: str = "pending",
    ) -> bool:
        """
        Save track metadata to STT service
        
        Args:
            egress_id: Unique egress identifier (used as _id)
            track_id: Track identifier
            room_ref_id: Reference to room document _id
            participant_identity: Participant identity
            audio_info: Dict containing {filename, ...}
            status: Track status (default: "pending")
            
        Returns:
            True if successful
        """
        try:
            if not self.mongodb_service.connected:
                await self.mongodb_service.connect()

            # Convert room_ref_id string to ObjectId
            try:
                room_ref_id = ObjectId(room_ref_id)
            except Exception as e:
                    logger.error(f"Invalid room_ref_id '{room_ref_id}': {e}")
                    return False
    
            # Check if room exists
            room = await self.mongodb_service.get_room_by_id(room_ref_id)
            if not room:
                logger.error(f"Room with ID '{room_ref_id}' not found")
                return False
            track_result = await self.mongodb_service.save_track_metadata(
                egress_id=egress_id,
                track_id=track_id,
                room_ref_id=room_ref_id,
                participant_identity=participant_identity,
                status=status,
            )

            if not track_result:
                logger.error(f"Failed to save track metadata for egress_id '{egress_id}'")
                return False

            return True

        except Exception as e:
            logger.exception(f"Failed to save track metadata: {e}")
            return False