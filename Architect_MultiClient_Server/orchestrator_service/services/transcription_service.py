from datetime import datetime
from typing import Any

from orchestrator_service.api.sse_metadata_api import metadata_channel
from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.transcription_task import TranscriptionTask
from orchestrator_service.models.webhook_models import EgressInfo
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.services.redis.redis_producer_service import (
    RedisProducerService,
    create_producer_service,
)
from orchestrator_service.services.summary_service import get_summary_service
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class TranscriptionService:
    """Service sent audio to transcription queue via Redis Stream"""

    def __init__(self):
        self.config = get_config().stt_service
        self.redis_config = get_config().redis
        self.api_url = f"http://{self.config.host}:{self.config.port}/api/transcribe"
        self.timeout = 30.0
        self.pg_repo = PgTranscriptRepository()
        self._redis_producer: RedisProducerService[TranscriptionTask] | None = None
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

    async def enqueue(self, egress_info: EgressInfo) -> bool:
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
                if not self.pg_repo.connected:
                    await self.pg_repo.connect()
                track_result = await self.pg_repo.save_track_metadata(
                    egress_id=egress_info.egress_id,
                    audio_info={
                        "filename": egress_info.filename,
                        "duration_sec": egress_info.duration,
                        "started_at_ns": egress_info.started_at,
                        "ended_at_ns": egress_info.ended_at,
                        "location": egress_info.location,
                        "source": egress_info.source,
                    },
                    status="wait_process",
                )

                if track_result and track_result.room_ref_id:
                    room = await self.pg_repo.check_event_record_done(track_result.room_ref_id)
                    if room:
                        await metadata_channel.push_room_record_done(
                            room_id=str(room.id),
                            room_name=room.room_name,
                        )
                elif track_result:
                    logger.warning(f"Track metadata saved but no room_ref_id found: {track_result}")
                else:
                    logger.warning("Failed to save track metadata: track_result is None")

                logger.info(f"✅ Track metadata updated: egress={egress_info.egress_id}")
            except Exception as e:
                logger.warning(f"Failed to update track metadata: {e}")
                # Continue processing even if metadata update fails

            producer = await self._get_producer()

            # Create task object
            task = TranscriptionTask(
                egress_id=egress_info.egress_id,
                filename=egress_info.filename,
                location=egress_info.location,
                duration=egress_info.duration,
                started_at=egress_info.started_at,
                ended_at=egress_info.ended_at,
                source=egress_info.source or "",
            )

            task_id = await producer.enqueue(task)

            logger.info(f"✓ Queued to Redis: {egress_info.egress_id} → {task_id}")
            return True

        except Exception as e:
            logger.error(f"✗ Redis enqueue failed: {e}")
            return False

    async def final_room(self, room_name: str, room_id: str) -> bool:
        """
        Mark room as finalized in transcription service

        Args:
            room_name: Name of the room
            room_id: Room ID

        Returns:
            True if successful
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            updated = await self.pg_repo.final_room_status(room_name=room_name, room_id=room_id)

            if not updated:
                return False

            if await self.pg_repo.check_event_record_done(room_id):
                await metadata_channel.push_room_record_done(room_id=room_id, room_name=room_name)

            if await self.pg_repo.check_and_complete_room(room_id):
                service = get_summary_service()
                await service.generate_summary(room_id)

            return True
        except Exception as e:
            logger.exception("Failed to end room transcription: %s", e)
            return False

    async def start_room(self, room_name: str) -> dict | None:
        """
        Notify transcription service to start room

        Returns:
            dict response if success, None if failed
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            room_id = await self.pg_repo.create_room_session(room_name=room_name)
            return {
                "success": True,
                "message": f"Room {room_name} started successfully",
                "room_id": room_id,
            }
        except Exception as e:
            logger.exception(f"✗ Unexpected error starting room: {e}")

        return None

    async def save_participant(
        self, room_id: str, participant_identity: str, timestamp: datetime | None = None
    ) -> bool:
        """
        Save participant info to PostgreSQL

        Args:
            participant_identity: Participant identity

        Returns:
            True if successful, False otherwise
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            result = await self.pg_repo.save_participant(
                room_id=room_id,
                participant_identity=participant_identity,
                timestamp=timestamp,
            )
            return result
        except Exception as e:
            logger.exception(f"Failed to save participant: {e}")
            return False

    async def save_participants_batch(self, room_id: str, participants: list[dict[str, Any]]) -> dict[str, int]:
        """
        Save batch of participants to PostgreSQL
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            result = await self.pg_repo.save_batch_participants(room_id=room_id, participants=participants)
            return result
        except Exception as e:
            logger.exception(f"Failed to save batch participants: {e}")
            return {"success": False, "added_count": 0, "skipped_count": 0}

    async def save_track_metadata(
        self,
        egress_id: str,
        track_id: str,
        room_ref_id: str,
        participant_identity: str,
        status: str = "pending",
    ) -> bool:
        """
        Save track metadata to STT service

        Args:
            egress_id: Unique egress identifier (used as id)
            track_id: Track identifier
            room_ref_id: Reference to room document id
            participant_identity: Participant identity
            audio_info: Dict containing {filename, ...}
            status: Track status (default: "pending")

        Returns:
            True if successful
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()

            # Check if room exists
            room = await self.pg_repo.get_room_by_id(room_ref_id)
            if not room:
                logger.error(f"Room with ID '{room_ref_id}' not found")
                return False
            track_result = await self.pg_repo.save_track_metadata(
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
