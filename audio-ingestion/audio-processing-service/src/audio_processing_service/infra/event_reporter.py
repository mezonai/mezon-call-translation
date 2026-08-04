"""EventReporter: plain HTTP POST to orchestrator, same endpoint/contract
record-service uses (audio-ingestion PLAN.md D8/D21).

Payload shape matches orchestrator_service/models/recording_event_models.py's
DerivativeEventRequest exactly. Deliberately thin, unlike record-service's
HttpEventReporter -- no local durable retry state here, because a failed
report simply raises and lets RedisDerivativeQueueService's reject()
retry/DLQ the whole task (audio-ingestion PLAN.md D28 point 3/D7: this
service is non-critical, retry-on-fail is already provided by the queue,
no need to duplicate it at the HTTP layer too).
"""

from __future__ import annotations

from typing import Optional

import httpx

from audio_processing_service.config import OrchestratorConfig


class EventReporter:
    def __init__(self, config: OrchestratorConfig) -> None:
        self._config = config
        # Eager, not lazy -- same reasoning as record-service's
        # HttpEventReporter (every call uses this client, construction does
        # no I/O, avoids a check-then-create race across concurrently
        # finalizing tasks).
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url, timeout=self._config.request_timeout_seconds
        )

    async def report_completed(self, *, recording_id: str, bucket: str, object_key: str) -> None:
        await self._post({
            "event": "derivative.completed",
            "recording_id": recording_id,
            "bucket": bucket,
            "object_key": object_key,
        })

    async def report_failed(self, *, recording_id: str, error: str) -> None:
        await self._post({
            "event": "derivative.failed",
            "recording_id": recording_id,
            "error": error[:500],
        })

    async def _post(self, payload: dict) -> None:
        headers = {"Authorization": f"Bearer {self._config.api_key}"} if self._config.api_key else {}
        response = await self._client.post(
            self._config.events_path, json=payload, headers=headers
        )
        response.raise_for_status()

    async def close(self) -> None:
        await self._client.aclose()
