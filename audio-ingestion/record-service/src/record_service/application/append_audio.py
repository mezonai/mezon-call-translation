"""Use case: feed raw PCM bytes (and/or an agent-side drop count) into a
session's multipart upload buffer, flushing full parts as they fill.

Dumb pass-through by design (PLAN.md D6) -- no decode, no re-encode. The only
"processing" here is chunking for S3's multipart API and the cheap D11/D12
sanity signals, both O(1) arithmetic on counters already being tracked.
"""

from __future__ import annotations

import logging
import time

from record_service.application.retry import with_retry
from record_service.application.session_registry import SessionRegistry
from record_service.domain.models import QualityAnnotation, RecordingSession, UploadedPart
from record_service.domain.policies import RecordingPolicy
from record_service.domain.ports import BlobStorage, SessionStateRepository

logger = logging.getLogger(__name__)


class AppendAudio:
    def __init__(
        self,
        registry: SessionRegistry,
        blob_storage: BlobStorage,
        state_repo: SessionStateRepository,
        policy: RecordingPolicy,
    ) -> None:
        self._registry = registry
        self._blob_storage = blob_storage
        self._state_repo = state_repo
        self._policy = policy

    async def execute(self, session_id: str, pcm: bytes | None, dropped_count: int = 0) -> bool:
        """Returns False if the session is unknown (adapter should stop sending)."""
        active = self._registry.get(session_id)
        if active is None:
            return False

        async with active.lock:
            session = active.session

            if dropped_count:
                session.dropped_frame_count += dropped_count
                self._maybe_annotate_quality(session)

            if not pcm:
                return True

            session.raw_bytes_received += len(pcm)
            session.frames_received += 1
            active.buffer.extend(pcm)

            while len(active.buffer) >= self._policy.part_size_bytes:
                payload = bytes(active.buffer[: self._policy.part_size_bytes])
                del active.buffer[: self._policy.part_size_bytes]
                await self._upload_part(session, payload)

            await self._state_repo.save(session)

        return True

    def _maybe_annotate_quality(self, session: RecordingSession) -> None:
        """PLAN.md D12: annotate only, never discard or restart the session."""
        if session.drop_rate() < self._policy.drop_rate_warning_threshold:
            return
        already_open = any(a.end_offset_ms is None for a in session.quality_annotations)
        if already_open:
            return
        offset_ms = int((time.time() - session.started_at) * 1000)
        session.quality_annotations.append(
            QualityAnnotation(start_offset_ms=offset_ms, reason="high_drop_rate")
        )
        logger.warning(
            "Session %s crossed drop-rate threshold (%.1f%%) at offset %dms -- annotated, not acted on",
            session.session_id,
            session.drop_rate() * 100,
            offset_ms,
        )

    async def _upload_part(self, session: RecordingSession, payload: bytes) -> None:
        part_number = session.next_part_number

        async def _do_upload():
            return await self._blob_storage.upload_part(
                session.bucket, session.object_key, session.upload_id, part_number, payload
            )

        etag = await with_retry(
            self._policy.upload_retry, f"upload_part[{session.session_id}#{part_number}]", _do_upload
        )
        session.parts.append(UploadedPart(part_number=part_number, etag=etag))
