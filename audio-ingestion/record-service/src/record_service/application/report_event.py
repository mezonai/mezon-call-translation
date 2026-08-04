"""Use case: tell orchestrator about a recording lifecycle event.

PLAN.md D8/D11: same reliability recipe as the MinIO upload path -- retry at
the call site, and on exhaustion fall back to the session's durable local
state (`reported: False`) so the reconciler (recover_orphaned_sessions.py)
retries later instead of the event silently vanishing.
"""

from __future__ import annotations

import asyncio
import logging

from record_service.domain.models import RecordingSession
from record_service.domain.policies import RetryPolicy
from record_service.domain.ports import EventReporter, SessionStateRepository

logger = logging.getLogger(__name__)


class ReportEvent:
    def __init__(
        self,
        event_reporter: EventReporter,
        state_repo: SessionStateRepository,
        policy: RetryPolicy,
    ) -> None:
        self._event_reporter = event_reporter
        self._state_repo = state_repo
        self._policy = policy

    async def execute(self, session: RecordingSession, event: str) -> bool:
        last_error: Exception | None = None
        for attempt in range(self._policy.max_attempts):
            try:
                ok = await self._event_reporter.report(session, event)
                if ok:
                    session.reported = True
                    session.last_report_error = None
                    await self._state_repo.save(session)
                    return True
                last_error = RuntimeError("orchestrator did not acknowledge event")
            except Exception as exc:  # noqa: BLE001 - retry boundary
                last_error = exc

            session.report_attempts += 1
            if attempt < self._policy.max_attempts - 1:
                await asyncio.sleep(self._policy.delay_for(attempt))

        # Exhausted: don't lose the event, just admit we couldn't deliver it
        # yet. The reconciler will keep retrying on its own schedule.
        session.reported = False
        session.last_report_error = str(last_error) if last_error else "unknown"
        await self._state_repo.save(session)
        logger.warning(
            "Failed to report %s for session %s after %d attempts: %s -- deferring to reconciler",
            event,
            session.session_id,
            self._policy.max_attempts,
            session.last_report_error,
        )
        return False
