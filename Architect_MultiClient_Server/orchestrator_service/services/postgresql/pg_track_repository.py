"""
PostgreSQL repository for `tracks` -- track-only write/update logic
(audio-ingestion PLAN.md D18/D26).

Deliberately a separate file from pg_transcript_repository.py: that file
predates the `develop` branch's in-progress ruff/mypy + ORM migration for
this service's repository layer, and is large enough that inserting new
methods into it invites merge conflicts with that work. New track-only
logic added here instead, already in the ORM style `develop` is moving the
rest of the layer towards (declarative `Track` model, not raw `text()` SQL)
-- this hotfix branched straight off `main` (not `develop`, which hasn't
been tested enough yet for a hotfix), so this is the cheapest way to keep
that merge clean later.

Room+track-mixed logic (`check_and_notify_room_recordings_ready`) stays in
pg_transcript_repository.py -- see that method's docstring for why.

Track PK (`tracks.id`) is named `record_id` throughout this file, not
`egress_id` -- that naming is a legacy egress-era holdover (PLAN.md D10
deferred renaming the actual DB column since it'd be a migration on live
data, but nothing forces new code to keep calling it "egress" too). It's
the same value as record-service's `recording_id`/`session_id`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import Track
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def _track_to_dict(track: Track) -> dict[str, Any]:  # type: ignore[explicit-any]
    return {
        "id": track.id,
        "track_id": track.track_id,
        "room_ref_id": str(track.room_ref_id) if track.room_ref_id else None,
        "participant_identity": track.participant_identity,
        "status": track.status,
        "derivative_status": track.derivative_status,
        "chunk_count": track.chunk_count,
        "audio_info": track.audio_info,
        "error": track.error,
        "created_at": track.created_at,
        "updated_at": track.updated_at,
    }


class PgTrackRepository:
    """ORM-backed repository for `tracks` -- see module docstring."""

    def __init__(self):
        pass

    async def create_track_placeholder(
        self,
        *,
        record_id: str,
        track_id: str | None,
        room_ref_id: str | None,
        participant_identity: str | None,
    ) -> None:
        """Eagerly registers a track that has started recording but not yet
        finished (audio-ingestion PLAN.md D26, `recording.started`).

        Deliberately `INSERT ... ON CONFLICT (id) DO NOTHING` rather than an
        upsert: `recording.started` is sent fire-and-forget from
        record-service and can arrive *after* `recording.completed`/`.failed`
        for the same track (very short recordings, or retried delivery) --
        an upsert would clobber an already-terminal row back to
        status="pending" with nothing left to ever move it forward again.
        ON CONFLICT DO NOTHING makes this pure "create the row if it doesn't
        exist yet, otherwise leave whatever is already there untouched"
        regardless of arrival order or duplicate delivery.
        """
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                stmt = (
                    pg_insert(Track)
                    .values(
                        id=record_id,
                        track_id=track_id,
                        room_ref_id=room_ref_id,
                        participant_identity=participant_identity,
                        status="pending",
                        derivative_status="pending",
                        created_at=now,
                        updated_at=now,
                    )
                    .on_conflict_do_nothing(index_elements=[Track.id])
                )
                await session.execute(stmt)
                await session.commit()
        except Exception as e:
            logger.error(f"Failed to create track placeholder: {e}")

    async def update_track_derivative(  # type: ignore[explicit-any]
        self,
        record_id: str,
        derivative_status: str,
        derivative_error: str | None = None,
        derivative_object_key: str | None = None,
    ) -> dict[str, Any] | None:
        """Updates a track's derivative pipeline state (audio-ingestion
        PLAN.md D18). Independent of `status` (STT) -- see
        save_track_metadata's derivative_status param below.
        """
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                track = await session.get(Track, record_id)
                if track is None:
                    logger.warning(f"update_track_derivative: no track found for id={record_id}")
                    return None

                track.derivative_status = derivative_status
                track.updated_at = now
                if derivative_error is not None:
                    track.error = derivative_error
                if derivative_object_key is not None:
                    # Folded into audio_info JSONB rather than a new column --
                    # this is auxiliary metadata, not something queried on.
                    # Reassigning (not mutating in place) so SQLAlchemy's
                    # change-tracking picks it up.
                    track.audio_info = {
                        **(track.audio_info or {}),
                        "derivative_object_key": derivative_object_key,
                    }

                await session.commit()
                return _track_to_dict(track)
        except Exception as e:
            logger.error(f"Failed to update track derivative status: {e}")
            return None

    async def save_track_metadata(  # type: ignore[explicit-any]
        self,
        *,
        record_id: str,
        track_id: str | None = None,
        room_ref_id: str | None = None,
        participant_identity: str | None = None,
        audio_info: dict[str, Any] | None = None,
        status: str = "pending",
        derivative_status: str | None = None,
        error: str | None = None,
    ) -> dict[str, Any] | None:
        """Upsert on `tracks.id` -- creates the row if `recording.started`
        (D26) never made it in first (e.g. still-open delivery retry), or
        updates whatever fields are given (only `status` is unconditional --
        the rest only apply if explicitly passed) if the row already exists.
        Used by both the STT path (`status`) and the capture-result path
        (`recording.completed`/`.failed`, PLAN.md D18).
        """
        if not record_id:
            logger.error("record_id is required")
            return None

        session_factory = get_session_factory()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                track = await session.get(Track, record_id)
                if track is not None:
                    if status:
                        track.status = status
                    if derivative_status is not None:
                        track.derivative_status = derivative_status
                    if audio_info is not None:
                        track.audio_info = audio_info
                    if error is not None:
                        track.error = error
                    track.updated_at = now
                else:
                    track = Track(
                        id=record_id,
                        track_id=track_id,
                        room_ref_id=room_ref_id,
                        participant_identity=participant_identity,
                        status=status,
                        derivative_status=derivative_status,
                        audio_info=audio_info,
                        error=error,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(track)

                await session.commit()
                logger.info(f"📝 Track metadata saved: id(record)={record_id}")
                return _track_to_dict(track)
        except Exception as e:
            logger.error(f"Failed to save track metadata: {e}")
            return None

    async def complete_track_with_vad_duration(
        self,
        track_ref_id: str,
        duration_after_vad_sec: float | None
    ) -> Track | None:
        session_factory = get_session_factory()

        async with session_factory() as session:
            stmt = (
                update(Track)
                .where(Track.id == track_ref_id)
                .values(
                    status="completed",
                    updated_at=datetime.now(UTC)
                )
            )
            if duration_after_vad_sec is not None:
                new_json = func.jsonb_build_object(
                    "duration_after_vad_sec",
                    duration_after_vad_sec
                )
                stmt = stmt.values(
                    audio_info=func.coalesce(Track.audio_info,
                        text("'{}'::jsonb")).concat(new_json)
                )
            stmt = stmt.returning(Track)

            try:
                result = await session.execute(stmt)
                updated_track = result.scalar_one_or_none()

                await session.commit()
                return updated_track

            except Exception:
                logger.error("Failed to complete track with VAD duration")
                return None
