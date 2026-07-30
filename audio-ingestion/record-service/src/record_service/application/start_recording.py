"""Use case: begin (or resume) capturing a track.

Called once per gRPC stream-open by infra/grpc/ingest_server.py. If a session
for the same (room_id, track_id) is sitting in GRACE_WAIT (PLAN.md D5 tier 2,
an abrupt disconnect that hasn't timed out yet), this resumes it instead of
opening a second multipart upload for the same track.
"""

from __future__ import annotations

import logging

from record_service.application.session_registry import ActiveSession, SessionRegistry
from record_service.domain.models import RecordingSession, RecordingStatus
from record_service.domain.ports import BlobStorage, SessionStateRepository

logger = logging.getLogger(__name__)


class StartRecording:
    def __init__(
        self,
        registry: SessionRegistry,
        blob_storage: BlobStorage,
        state_repo: SessionStateRepository,
    ) -> None:
        self._registry = registry
        self._blob_storage = blob_storage
        self._state_repo = state_repo

    async def execute(
        self,
        room_id: str,
        track_id: str,
        participant_identity: str,
        source: str,
        sample_rate: int,
        channels: int,
        bucket: str,
        object_key: str,
    ) -> RecordingSession:
        session_id = RecordingSession.make_session_id(room_id, track_id)

        # Serializes the whole "does this session already exist / create a
        # new one" decision, scoped to this session_id only (SessionRegistry
        # .creation_lock, not a service-wide lock -- see its docstring).
        # Without this, two concurrent execute() calls for the same
        # not-yet-created session_id can both observe `existing is None`
        # (registry.get has no lock, and create_multipart_upload below
        # awaits), open two separate multipart uploads, and the second
        # registry.put() silently clobbers the first -- leaking an upload
        # nothing will ever complete/abort.
        async with self._registry.creation_lock(session_id):
            existing = self._registry.get(session_id)
            if existing is not None:
                resumed = await self._try_resume_or_reuse(existing, session_id)
                if resumed is not None:
                    return resumed
                # existing was FINALIZING/COMPLETED/FAILED -- a concurrent
                # grace-period timeout claimed it (see stop_recording.py
                # _grace_timeout) while we were deciding. Stale entry, drop
                # it and fall through to open a fresh session below.
                self._registry.pop(session_id)

            upload_id = await self._blob_storage.create_multipart_upload(bucket, object_key)
            session = RecordingSession(
                room_id=room_id,
                track_id=track_id,
                participant_identity=participant_identity,
                source=source,
                sample_rate=sample_rate,
                channels=channels,
                bucket=bucket,
                object_key=object_key,
                upload_id=upload_id,
            )
            self._registry.put(ActiveSession(session))
            await self._state_repo.save(session)
            logger.info(
                "Started session %s -> s3://%s/%s (upload_id=%s)",
                session_id,
                bucket,
                object_key,
                upload_id,
            )
            return session

    async def _try_resume_or_reuse(
        self, existing: ActiveSession, session_id: str
    ) -> RecordingSession | None:
        """Returns the session if it could be resumed/reused, else None if the
        entry turned out to be stale (already on its way to finalized).

        Locks `existing.lock` -- the same lock stop_recording.py's
        _grace_timeout acquires before deciding to finalize a GRACE_WAIT
        session, so exactly one of "resume" or "finalize" wins the race,
        never both.
        """
        async with existing.lock:
            status = existing.session.status
            if status == RecordingStatus.GRACE_WAIT:
                # Reconnect within the grace window -- resume the same
                # upload_id, cancel the pending finalize timer. PLAN.md D5 tier 2.
                if existing.grace_task is not None:
                    existing.grace_task.cancel()
                    existing.grace_task = None
                existing.session.status = RecordingStatus.RECORDING
                await self._state_repo.save(existing.session)
                logger.info("Resumed session %s within grace period", session_id)
                return existing.session
            if status == RecordingStatus.RECORDING:
                # Duplicate start on an already-live session (e.g. agent
                # retried before noticing the first stream was accepted).
                # Idempotent: hand back the same session.
                logger.warning("Duplicate start_recording for live session %s", session_id)
                return existing.session
            return None
