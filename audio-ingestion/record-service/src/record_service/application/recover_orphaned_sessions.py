"""Use case: reconcile durable state left behind by a crashed/restarted
process. PLAN.md D5 tier 3.

Runs once at startup (bootstrap.py) and again on a timer (main.py), because
two different things can leave a state entry stranded:
  1. The process died mid-session -- no live gRPC stream can possibly still
     be attached to it (a fresh process has an empty SessionRegistry), so
     there is nothing to "resume": finalize with whatever parts made it to
     S3 before the crash.
  2. The session finished fine, but reporting the event to orchestrator kept
     failing (D8) -- state is already terminal, just needs another delivery
     attempt.
Anything still on disk falls into one of these two buckets; this use case
handles both with the same pass.
"""

from __future__ import annotations

import logging

from record_service.application.finalize import complete_or_abort
from record_service.application.report_event import ReportEvent
from record_service.application.session_registry import SessionRegistry
from record_service.domain.models import RecordingStatus
from record_service.domain.policies import RecordingPolicy
from record_service.domain.ports import BlobStorage, SessionStateRepository

logger = logging.getLogger(__name__)

_NON_TERMINAL = {RecordingStatus.RECORDING, RecordingStatus.GRACE_WAIT, RecordingStatus.FINALIZING}


class RecoverOrphanedSessions:
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

    async def execute(self) -> int:
        """Returns the number of sessions still unresolved after this pass."""
        sessions = await self._state_repo.list_unfinished()
        if not sessions:
            return 0

        logger.info("Reconciling %d unfinished session(s) from durable state", len(sessions))
        remaining = 0
        for session in sessions:
            if self._registry.get(session.session_id) is not None:
                # Live in this process instance -- a real task owns it and is
                # the one persisting its state (AppendAudio saves after every
                # part flush, so an active RECORDING session legitimately
                # shows up here). Touching it would race with that task and
                # could complete/abort an in-progress upload out from under
                # it. Nothing to reconcile; it'll persist itself when done.
                continue

            if session.status in _NON_TERMINAL:
                logger.warning(
                    "Session %s left in status=%s by a previous process instance -- "
                    "finalizing with %d part(s) already uploaded",
                    session.session_id,
                    session.status.value,
                    len(session.parts),
                )
                session.ended_at = session.ended_at or session.started_at
                await complete_or_abort(session, self._blob_storage, self._policy)
                await self._state_repo.save(session)

            event = (
                "recording.completed" if session.status == RecordingStatus.COMPLETED else "recording.failed"
            )
            reported = await self._report_event.execute(session, event)
            if reported:
                await self._state_repo.delete(session.session_id)
            else:
                remaining += 1

        return remaining
