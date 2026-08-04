"""Shared complete/abort logic for a RecordingSession's multipart upload.

Used by both stop_recording.py (live session, still in the registry) and
recover_orphaned_sessions.py (session recovered from durable state after a
crash, PLAN.md D5 tier 3) -- the S3-facing half of finalizing is identical
either way, only where the session/buffer comes from differs.
"""

from __future__ import annotations

import logging

from record_service.application.retry import with_retry
from record_service.domain.models import QualityAnnotation, RecordingSession, RecordingStatus
from record_service.domain.policies import RecordingPolicy
from record_service.domain.ports import BlobStorage

logger = logging.getLogger(__name__)


async def complete_or_abort(
    session: RecordingSession, blob_storage: BlobStorage, policy: RecordingPolicy
) -> None:
    """Mutates session.status to COMPLETED or FAILED."""
    if not session.parts:
        await _abort(session, blob_storage, policy)
        return

    try:
        await with_retry(
            policy.upload_retry,
            f"complete_multipart[{session.session_id}]",
            lambda: blob_storage.complete_multipart_upload(
                session.bucket, session.object_key, session.upload_id, session.parts
            ),
        )
        session.status = RecordingStatus.COMPLETED
        if session.is_byte_rate_suspect(policy.byte_rate_tolerance):
            # D11: upload succeeded, but content looks thin relative to
            # elapsed time -- surface it, don't hide it as a clean `completed`.
            # An annotation (not just a log line) so it survives into the
            # reported event and is actually "quan sát được" downstream.
            session.quality_annotations.append(
                QualityAnnotation(
                    start_offset_ms=0,
                    end_offset_ms=int(((session.ended_at or 0) - session.started_at) * 1000),
                    reason="low_byte_rate",
                )
            )
            logger.warning(
                "Session %s completed but byte rate looks low (%.0f bytes for %.1fs) -- flagged low_byte_rate",
                session.session_id,
                session.raw_bytes_received,
                (session.ended_at or 0) - session.started_at,
            )
    except Exception as exc:  # noqa: BLE001 - retry already exhausted upstream
        logger.error("Failed to complete multipart upload for %s: %s", session.session_id, exc)
        session.status = RecordingStatus.FAILED


async def _abort(session: RecordingSession, blob_storage: BlobStorage, policy: RecordingPolicy) -> None:
    logger.info("No data uploaded for %s, aborting multipart upload", session.session_id)
    try:
        await with_retry(
            policy.upload_retry,
            f"abort_multipart[{session.session_id}]",
            lambda: blob_storage.abort_multipart_upload(
                session.bucket, session.object_key, session.upload_id
            ),
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("Failed to abort multipart upload for %s: %s", session.session_id, exc)
    session.status = RecordingStatus.FAILED
