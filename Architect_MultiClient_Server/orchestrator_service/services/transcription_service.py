from datetime import datetime
from typing import Any

from orchestrator_service.api.sse_metadata_api import metadata_channel
from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.transcription_task import TranscriptionTask
from orchestrator_service.services.postgresql.pg_track_repository import PgTrackRepository
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
        self.track_repo = PgTrackRepository()
        self._redis_producer: RedisProducerService[TranscriptionTask] | None = None
        self.stream_key = "transcription:stream"

    async def _get_producer(self) -> RedisProducerService[TranscriptionTask]:
        """Get or create Redis producer (lazy initialization)."""
        if not self._redis_producer:
            self._redis_producer = create_producer_service(
                task_class=TranscriptionTask,
                stream_key=self.stream_key,
            )
        return self._redis_producer

    async def handle_recording_completed(
        self,
        *,
        recording_id: str,
        track_id: str,
        room_ref_id: str,
        participant_identity: str,
        filename: str,
        location: str,
        duration: str,
        started_at: str,
        ended_at: str,
        source: str = "",
        skip_stt: bool = False,
    ) -> bool:
        """
        Raw capture done (record-service's recording.completed, audio-ingestion
        PLAN.md D18) -- save track metadata (upserts: the row normally
        already exists from `recording.started`, PLAN.md D26, but this must
        still work standalone in case that event never made it) and kick off
        Whisper STT via transcription:stream. Does NOT touch room_record_done
        -- that fires on the separate, later derivative-completion path
        (D19), not here.

        skip_stt: the agent's own TTS track (PLAN.md D3x) -- still needs the
        track row (room-completion gating counts it), just not a Whisper job;
        RecordingEventService.handle_tts_transcript_event is what eventually
        flips this row to "completed" instead.

        Replaces the old enqueue(egress_info: Dict) which took a
        LiveKit-egress-webhook-shaped dict (audio-ingestion PLAN.md D2: no
        egress-shaped contracts survive the migration).
        """
        try:
            try:
                track_result = await self.track_repo.save_track_metadata(
                    record_id=recording_id,
                    track_id=track_id,
                    room_ref_id=room_ref_id,
                    participant_identity=participant_identity,
                    audio_info={
                        "filename": filename,
                        "duration_sec": duration,
                        "started_at_ns": started_at,
                        "ended_at_ns": ended_at,
                        "location": location,
                        "source": source,
                    },
                    status="wait_process",
                    derivative_status="pending",
                )
                if not track_result:
                    logger.warning(f"Failed to save track metadata for recording_id={recording_id}")

                logger.info(f"✅ Track metadata updated: recording_id={recording_id}")
            except Exception as e:
                logger.warning(f"Failed to update track metadata: {e}")
                # Continue processing even if metadata update fails

            if skip_stt:
                logger.info(f"Skipping Whisper STT enqueue for recording_id={recording_id} (agent TTS track)")
                return True

            producer = await self._get_producer()

            task = TranscriptionTask(
                egress_id=recording_id,
                filename=filename,
                location=location,
                duration=duration,
                started_at=started_at,
                ended_at=ended_at,
                source=source,
            )

            task_id = await producer.enqueue(task)

            logger.info(f"✓ Queued to Redis: {recording_id} → {task_id}")
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

            # Second of the two call sites required by D19 -- the other is
            # RecordingEventService.handle_derivative_event(). Whichever
            # condition (room finalized vs. last track's derivative done)
            # is satisfied last is the one that actually fires the event;
            # the atomic UPDATE guard inside makes this safe to call from both.
            if await self.pg_repo.check_and_notify_room_recordings_ready(room_id):
                await metadata_channel.push_room_record_done(
                    room_id=str(room_id), room_name=room_name
                )

            if await self.pg_repo.check_and_complete_room(room_id):
                service = get_summary_service()
                await service.generate_summary(room_id)

            return True
        except Exception as e:
            logger.exception("Failed to end room transcription: %s", e)
            return False

    async def start_room(self, room_id: str, room_name: str) -> bool:
        """Create the room row for an agent-supplied session id (audio-ingestion
        PLAN.md D27x). Idempotent (see create_room_session) -- a retried
        /register with the same room_id is a no-op, not an error."""
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            return await self.pg_repo.create_room_session(room_id=room_id, room_name=room_name)
        except Exception as e:
            logger.exception(f"✗ Unexpected error starting room: {e}")
            return False

    async def save_participant(
        self, room_id: str, participant_identity: str, timestamp: datetime | None = None, username: str | None = None
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
                username=username,
            )
            return result
        except Exception as e:
            logger.exception(f"Failed to save participant: {e}")
            return False

    async def save_participants_batch(  # type: ignore[explicit-any]
        self, room_id: str, participants: list[dict[str, Any]]
    ) -> bool:
        """
        Save batch of participants to PostgreSQL. Uses the race-safe
        save_batch_participants_atomic (audio-ingestion PLAN.md D27) --
        the older save_batch_participants has a TOCTOU race against
        concurrent save_participant() calls from the participant_joined
        webhook (see that method's docstring in pg_transcript_repository.py).
        """
        try:
            if not self.pg_repo.connected:
                await self.pg_repo.connect()
            return await self.pg_repo.save_batch_participants_atomic(
                room_id=room_id, participants=participants
            )
        except Exception as e:
            logger.exception(f"Failed to save batch participants: {e}")
            return False
