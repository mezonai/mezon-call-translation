"""Use case: end a session, either immediately (graceful) or after a grace
window (abrupt disconnect). PLAN.md D5.

- graceful=True: agent closed the stream on purpose (track unpublished,
  agent shutdown). Finalize now, no reason to wait.
- graceful=False: the gRPC stream broke unexpectedly. The agent might just
  be riding out a network blip and about to reconnect with the same
  session_id (see start_recording.py) -- so we wait grace_period_seconds
  before finalizing best-effort with whatever was uploaded so far.
"""

from __future__ import annotations

import asyncio
import logging
import time

from record_service.application.finalize import complete_or_abort
from record_service.application.report_event import ReportEvent
from record_service.application.retry import with_retry
from record_service.application.session_registry import SessionRegistry
from record_service.domain.models import RecordingStatus, UploadedPart
from record_service.domain.policies import RecordingPolicy
from record_service.domain.ports import BlobStorage, SessionStateRepository

logger = logging.getLogger(__name__)


class StopRecording:
    def __init__(
        self,
        registry: SessionRegistry,
        blob_storage: BlobStorage,
        state_repo: SessionStateRepository,
        report_event: ReportEvent,
        policy: RecordingPolicy,
    ) -> None:
        self._registry = registry
        self._blob_storage = blob_storage
        self._state_repo = state_repo
        self._report_event = report_event
        self._policy = policy

    async def execute(self, session_id: str, graceful: bool) -> None:
        active = self._registry.get(session_id)
        if active is None:
            return

        if graceful:
            if active.grace_task is not None:
                active.grace_task.cancel()
            await self._finalize(session_id)
            return

        async with active.lock:
            active.session.status = RecordingStatus.GRACE_WAIT
            await self._state_repo.save(active.session)
        logger.info(
            "Session %s dropped abruptly, entering grace period (%.0fs)",
            session_id,
            self._policy.grace_period_seconds,
        )
        active.grace_task = asyncio.create_task(self._grace_timeout(session_id))

    async def _grace_timeout(self, session_id: str) -> None:
        try:
            await asyncio.sleep(self._policy.grace_period_seconds)
        except asyncio.CancelledError:
            return  # resumed via start_recording.py before the timer fired

        active = self._registry.get(session_id)
        if active is None:
            return

        # Claim the session under its lock before finalizing -- this is the
        # other half of the coordination in start_recording.py's
        # _try_resume_or_reuse (same active.lock object). Whichever of
        # "resume" or "finalize-on-timeout" acquires the lock first wins;
        # the loser sees a status that no longer matches what it expected and
        # backs off, so we never resume a session that's already being
        # finalized nor finalize one that just got resumed.
        async with active.lock:
            if active.session.status != RecordingStatus.GRACE_WAIT:
                return  # resumed already
            active.session.status = RecordingStatus.FINALIZING

        logger.info("Grace period expired for %s, finalizing best-effort", session_id)
        await self._finalize(session_id)

    async def _finalize(self, session_id: str) -> None:
        active = self._registry.pop(session_id)
        if active is None:
            return
        session = active.session

        async with active.lock:
            session.ended_at = time.time()

            if active.buffer:
                part_number = session.next_part_number
                payload = bytes(active.buffer)
                active.buffer.clear()
                try:

                    async def _do_upload():
                        return await self._blob_storage.upload_part(
                            session.bucket,
                            session.object_key,
                            session.upload_id,
                            part_number,
                            payload,
                        )

                    etag = await with_retry(
                        self._policy.upload_retry, f"final_part[{session_id}]", _do_upload
                    )
                    session.parts.append(UploadedPart(part_number=part_number, etag=etag))
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to flush final part for %s, finalizing with parts uploaded so far: %s",
                        session_id,
                        exc,
                    )

            await complete_or_abort(session, self._blob_storage, self._policy)
            await self._state_repo.save(session)

        event = "recording.completed" if session.status == RecordingStatus.COMPLETED else "recording.failed"
        reported = await self._report_event.execute(session, event)
        if reported:
            await self._state_repo.delete(session_id)
