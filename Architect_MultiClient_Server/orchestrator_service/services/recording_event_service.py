"""
Recording Event Service - handles events posted by record-service
(recording.started/recording.completed/recording.failed) and, from Phase 5,
audio-processing-service (derivative.completed/derivative.failed).

See audio-ingestion/PLAN.md D18/D19/D26. Deliberately not shaped like the old
LiveKit-egress webhook_handler.py flow it replaces (PLAN.md D2) -- this is
the new, clean entrypoint.
"""

import uuid

from orchestrator_service.api.sse_metadata_api import metadata_channel
from orchestrator_service.models.recording_event_models import (
    DerivativeEventRequest,
    RecordingEventRequest,
    RecordingEventResponse,
)
from orchestrator_service.services.audio_derivative_service import get_audio_derivative_service
from orchestrator_service.services.postgresql.pg_track_repository import PgTrackRepository
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class RecordingEventService:
    def __init__(self):
        self.pg_repo = PgTranscriptRepository()
        self.track_repo = PgTrackRepository()
        self.transcription_service = TranscriptionService()
        self.audio_derivative_service = get_audio_derivative_service()

    async def _resolve_room_ref_id(self, raw_room_id: str) -> str | None:
        """record-service sends whatever the agent gave it as room_id
        (audio-ingestion PLAN.md D27). Normally that's the agent's own
        stable orchestrator room UUID, captured once at registration --
        in which case this is a direct existence check, no registry lookup
        needed at all (and no window where a reused room_name could steer
        a late event to the wrong room, which resolving by name on every
        event used to risk).

        Falls back to the old name-registry resolve only if the value isn't
        a valid UUID -- the agent's degrade path when it couldn't get a
        room_id in time (orchestrator unreachable during registration) and
        had to fall back to ctx.room.name itself (see event_handlers.py).
        Not the common case, kept for backward/degrade compatibility.
        """
        try:
            uuid.UUID(raw_room_id)
        except (ValueError, AttributeError, TypeError):
            return await get_room_registry().get_room_id(raw_room_id)

        room = await self.pg_repo.get_room_by_id(raw_room_id)
        return raw_room_id if room else None

    async def handle_recording_event(self, payload: RecordingEventRequest) -> RecordingEventResponse:
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

        room_ref_id = await self._resolve_room_ref_id(payload.room_id)
        if not room_ref_id:
            logger.error(
                f"Room '{payload.room_id}' not registered, dropping {payload.event} for {payload.recording_id}"
            )
            return RecordingEventResponse(received=True, action="room_not_registered")

        if payload.event == "recording.started":
            # Eagerly registers the track (PLAN.md D26, partial reversal of
            # D22) so a track that's still recording has a row the whole
            # time it's in flight -- check_and_notify_room_recordings_ready/
            # check_and_complete_room's "NOT EXISTS non-terminal track" gates
            # are blind to tracks with no row at all, which let room
            # finalization race ahead of a track that hadn't reported
            # anything yet. ON CONFLICT DO NOTHING at the SQL level (not
            # save_track_metadata's upsert) -- this is sent fire-and-forget
            # from record-service and can arrive after the terminal event for
            # very short recordings; must never clobber an already-terminal
            # row back to "pending".
            await self.track_repo.create_track_placeholder(
                record_id=payload.recording_id,
                track_id=payload.track_id,
                room_ref_id=room_ref_id,
                participant_identity=payload.participant_identity,
            )
            return RecordingEventResponse(received=True, action="recording_started")

        if payload.event == "recording.completed":
            await self.transcription_service.handle_recording_completed(
                recording_id=payload.recording_id,
                track_id=payload.track_id,
                room_ref_id=room_ref_id,
                participant_identity=payload.participant_identity,
                filename=payload.object_key,
                location=f"s3://{payload.bucket}/{payload.object_key}",
                duration=str(payload.duration_seconds or 0),
                # record-service's started_at/ended_at are time.time() epoch
                # SECONDS (float) -- but audio_info stores them under
                # started_at_ns/ended_at_ns (summary_service.py and
                # room_service.py::get_audio_info() both parse/return them
                # as nanosecond epoch integers, a convention that predates
                # record-service and matched the old LiveKit-egress webhook
                # payload). Convert here, once, at the single point these
                # values enter orchestrator -- every downstream consumer
                # already assumes nanoseconds, so this is the one place
                # that needs the unit fix, not each consumer.
                started_at=str(round(payload.started_at * 1_000_000_000)),
                ended_at=str(round((payload.ended_at or payload.started_at) * 1_000_000_000)),
                source=payload.source,
            )
            await self.audio_derivative_service.enqueue(
                track_id=payload.recording_id,
                room_id=room_ref_id,
                bucket=payload.bucket,
                object_key=payload.object_key,
            )
            return RecordingEventResponse(received=True, action="recording_completed")

        if payload.event == "recording.failed":
            await self.track_repo.save_track_metadata(
                record_id=payload.recording_id,
                track_id=payload.track_id,
                room_ref_id=room_ref_id,
                participant_identity=payload.participant_identity,
                status="failed",
                derivative_status="failed",  # nothing to transcode from a failed capture
                error="record-service reported recording.failed",
            )
            # Sets a terminal derivative_status directly (unlike
            # recording.completed, there's no derivative job to wait on) --
            # so this can just as well be the track that satisfies D19's
            # room-ready condition. Same call site pattern as
            # handle_derivative_event below.
            if room_ref_id and await self.pg_repo.check_and_notify_room_recordings_ready(room_ref_id):
                room = await self.pg_repo.get_room_by_id(room_ref_id)
                if room:
                    await metadata_channel.push_room_record_done(
                        room_id=room_ref_id, room_name=room.room_name or ""
                    )
            return RecordingEventResponse(received=True, action="recording_failed")

        logger.warning(f"Unknown recording event type: {payload.event}")
        return RecordingEventResponse(received=True, action="ignored")

    async def handle_derivative_event(self, payload: DerivativeEventRequest) -> RecordingEventResponse:
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

        track = await self.pg_repo.get_track_by_id(payload.recording_id)
        if not track:
            logger.error(f"Unknown track for derivative event: {payload.recording_id}")
            return RecordingEventResponse(received=True, action="track_not_found")

        if payload.event not in ("derivative.completed", "derivative.failed"):
            logger.warning(f"Unknown derivative event type: {payload.event}")
            return RecordingEventResponse(received=True, action="ignored")

        derivative_status = "completed" if payload.event == "derivative.completed" else "failed"
        await self.track_repo.update_track_derivative(
            record_id=payload.recording_id,
            derivative_status=derivative_status,
            derivative_error=payload.error if payload.event == "derivative.failed" else None,
            derivative_object_key=payload.object_key if payload.event == "derivative.completed" else None,
        )

        # First of the two call sites required by D19 -- the other is
        # TranscriptionService.final_room(). Either the room finalizes last
        # (this call catches it) or this track's derivative finishes last
        # (this call catches it); the atomic UPDATE guard makes it safe to
        # check from both without double-firing.
        room_ref_id: str | None = str(track.room_ref_id) if track.room_ref_id else None
        if room_ref_id and await self.pg_repo.check_and_notify_room_recordings_ready(room_ref_id):
            room = await self.pg_repo.get_room_by_id(room_ref_id)
            if room:
                await metadata_channel.push_room_record_done(
                    room_id=str(room_ref_id), room_name=room.room_name or ""
                )

        return RecordingEventResponse(received=True, action=f"derivative_{derivative_status}")
