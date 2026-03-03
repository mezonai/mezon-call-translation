import httpx
from typing import Dict, Any
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.mongodb_service import get_mongodb_service
from typing import Optional
from bson import ObjectId

logger = get_logger(__name__)


class TranscriptionService:
    """Service sent audio to transcription queue"""
    
    def __init__(self):
        self.config = get_config().stt_service
        self.api_url =(f"http://{self.config.host}:{self.config.port}/api/transcribe")
        self.timeout = 30.0
        self.mongodb_service = get_mongodb_service()

    async def enqueue(self, egress_info: Dict) -> bool:
        """
        Send egress info to transcription queue
        
        Returns:
            True if successful, False if failed
        """
        try:
            try:
                if not self.mongodb_service.connected:
                    await self.mongodb_service.connect()
                await self.mongodb_service.save_track_metadata(            
                    egress_id=egress_info.get("egressId"),
                    audio_info={
                        "filename": egress_info.get("filename"),
                        "duration_sec": egress_info.get("duration"),
                        "started_at_ns": egress_info.get("startedAt"),
                        "ended_at_ns": egress_info.get("endedAt"),
                        "location": egress_info.get("location"),
                        "source": egress_info.get("source")
                    })
                logger.info(f"✅ Track metadata updated: egress={egress_info.get('egressId')}")
            except Exception as e:
                logger.warning(f"Failed to update track metadata: {e}")
                # Continue processing even if metadata update fails
            
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
            track_id_result = await self.mongodb_service.save_track_metadata(
                egress_id=egress_id,
                track_id=track_id,
                room_ref_id=room_ref_id,
                participant_identity=participant_identity,
                status=status,
            )

            if not track_id_result:
                logger.error(f"Failed to save track metadata for egress_id '{egress_id}'")
                return False

            return True

        except Exception as e:
            logger.exception(f"Failed to save track metadata: {e}")
            return False