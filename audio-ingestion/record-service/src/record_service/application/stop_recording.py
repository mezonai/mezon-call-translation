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
        active = self._registry.get(session_id)
        if active is None:
            return
        session = active.session

        async with active.lock:
            if session.status in (RecordingStatus.COMPLETED, RecordingStatus.FAILED):
                # A concurrent call already finalized this session_id -- e.g.
                # _grace_timeout finished its status check and released the
                # lock right before a graceful stream-end for the same
                # session_id called execute()->_finalize() directly (no
                # status/lock check there before calling in). Both then
                # reach here with the same non-popped `active` (see this
                # method's registry.get, not .pop, at the top -- pop() used
                # to double as a single-call guard when it ran first; moving
                # it to the end for the reconciler-race fix above removed
                # that side effect, so this checks explicitly instead).
                # Redoing complete_or_abort below would call
                # complete_multipart_upload/abort a second time on an
                # upload_id S3 already closed -- that fails, and the
                # exception handler in finalize.py would flip an already-
                # COMPLETED session to FAILED and report the wrong event.
                logger.info(
                    "Session %s already finalized (status=%s), skipping duplicate finalize call",
                    session_id,
                    session.status.value,
                )
                return

            session.ended_at = time.time()
            logger.info(
                "Finalizing session %s: buffered=%dB pending flush, raw_bytes_received=%d, "
                "frames_received=%d, dropped=%d, parts_uploaded=%d",
                session_id,
                len(active.buffer),
                session.raw_bytes_received,
                session.frames_received,
                session.dropped_frame_count,
                len(session.parts),
            )

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
                    logger.info(
                        "Uploaded final part %d (%d bytes) for %s", part_number, len(payload), session_id
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.error(
                        "Failed to flush final part for %s, finalizing with parts uploaded so far: %s",
                        session_id,
                        exc,
                    )

            await complete_or_abort(session, self._blob_storage, self._policy)
            await self._state_repo.save(session)

        self._registry.pop(session_id)

        logger.info(
            "Finalized session %s: status=%s, parts=%d, raw_bytes_received=%d, duration=%.1fs",
            session_id,
            session.status.value,
            len(session.parts),
            session.raw_bytes_received,
            (session.ended_at or 0) - session.started_at,
        )

        event = "recording.completed" if session.status == RecordingStatus.COMPLETED else "recording.failed"
        reported = await self._report_event.execute(session, event)
        if reported:
            await self._state_repo.delete(session_id)
