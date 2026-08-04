"""EventReporter adapter: plain HTTP POST to orchestrator (PLAN.md D8).

Deliberately NOT shaped like a LiveKit egress webhook (no EgressInfo/
EgressWebhook envelope, see PLAN.md D2) -- a clean, self-describing payload
instead. Each event carries the full session snapshot so the orchestrator
endpoint can upsert on `recording_id` regardless of delivery order: retries
of the same event, `recording.completed`/`.failed` arriving for a track
orchestrator has no prior row for (save_track_metadata's upsert creates the
row), or `recording.started` (PLAN.md D26) arriving after the terminal event
for a very short-lived track (orchestrator's placeholder insert is
ON CONFLICT DO NOTHING, so it never clobbers a row that already finished).
"""

from __future__ import annotations

from dataclasses import asdict

import httpx

from record_service.config import OrchestratorConfig
from record_service.domain.models import RecordingSession
from record_service.domain.ports import EventReporter


class HttpEventReporter(EventReporter):
    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config
        # Eager, not lazy: every reported event uses this client (there's no
        # "constructed but never called" case), and httpx.AsyncClient(...)
        # does no I/O at construction time -- it only opens connections
        # lazily on first request. Building it here avoids the same
        # check-then-create race S3BlobStorage had (ReportEvent can be
        # called concurrently by multiple sessions finalizing at once).
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url, timeout=self._config.request_timeout_seconds
        )

    async def report(self, session: RecordingSession, event: str) -> bool:
        headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
        response = await self._client.post(
            self._config.events_path, json=_to_payload(session, event), headers=headers
        )
        if response.status_code >= 500:
            response.raise_for_status()  # let with_retry() in report_event.py retry server errors
        return response.status_code < 400  # 4xx = orchestrator rejected it, not a transient failure

    async def close(self) -> None:
        await self._client.aclose()


def _to_payload(session: RecordingSession, event: str) -> dict:
    duration_seconds = None
    if session.ended_at is not None:
        duration_seconds = session.ended_at - session.started_at

    return {
        "event": event,
        "recording_id": session.session_id,
        "room_id": session.room_id,
        "track_id": session.track_id,
        "participant_identity": session.participant_identity,
        "source": session.source,
        "sample_rate": session.sample_rate,
        "channels": session.channels,
        "bucket": session.bucket,
        "object_key": session.object_key,
        "status": session.status.value,
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "duration_seconds": duration_seconds,
        "raw_bytes_received": session.raw_bytes_received,
        "dropped_frame_count": session.dropped_frame_count,
        "quality_annotations": [asdict(a) for a in session.quality_annotations],
    }
