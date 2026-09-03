"""Delete raw PCM after both of its independent consumers have finished.

`recording.completed` starts Whisper transcription and OGG derivative creation
in parallel.  Deleting from either completion handler unconditionally is
therefore unsafe: the other worker may not have downloaded the PCM yet.

This service uses the two existing Track statuses as a synchronization barrier:

    status == "completed"             (Whisper finished)
    derivative_status == "completed"  (OGG uploaded)

Both completion paths call `maybe_delete_raw_pcm` for prompt cleanup.  The
periodic reconciler calls the same idempotent helper to cover the crash window
where the second status was committed but that process died before its cleanup
call.  The only persisted cleanup marker (`raw_deleted_at`) lives inside the
existing `audio_info` JSONB document; no schema migration is required.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig

from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.postgresql.pg_track_repository import PgTrackRepository
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

RECONCILE_INTERVAL_SECONDS = 60
RECONCILE_BATCH_SIZE = 100


@singleton
class RawPcmCleanupService:
    """Shared cleanup helper plus its periodic missed-work reconciler."""

    def __init__(self):
        self._config = get_config().minio
        self._track_repo = PgTrackRepository()
        self._running = False
        self._reconciler_task: asyncio.Task[None] | None = None

        # boto3 is synchronous, so every network call below is moved to a
        # worker thread with asyncio.to_thread.  Path-style addressing matches
        # the MinIO setup used elsewhere in this repository and is accepted by
        # S3-compatible cloud storage too.
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self._config.endpoint,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret,
            region_name=self._config.region,
            config=BotoConfig(s3={"addressing_style": "path"}),
        )

    async def maybe_delete_raw_pcm(self, track_id: str) -> bool:
        """Delete one PCM only when Whisper and the OGG derivative succeeded.

        Returns True when the PCM is already deleted or was deleted by this
        call, and False when the track is not eligible yet.  Storage/DB errors
        are raised so callers can log them, but completion handlers deliberately
        catch those errors: cleanup failure must never turn a successful
        transcription or derivative event into a failed/retried main task.

        The fresh read here is essential.  In particular,
        `handle_derivative_event` fetched its original Track before committing
        the derivative update, so that object must not be reused for this check.
        """
        if not self._config.enabled:
            return False

        track = await self._track_repo.get_track_by_id(track_id)
        if track is None:
            return False
        if track.status != "completed" or track.derivative_status != "completed":
            return False

        audio_info = track.audio_info or {}
        if audio_info.get("raw_deleted_at"):
            return True

        raw_key = str(audio_info.get("filename") or "")
        derivative_key = str(audio_info.get("derivative_object_key") or "")
        location = str(audio_info.get("location") or "")
        if not raw_key.endswith(".pcm") or not derivative_key or not location:
            return False

        bucket = self._bucket_from_location(location)

        # Do not remove the only durable copy merely because the DB says a
        # derivative event arrived.  HEAD verifies the uploaded replacement is
        # still present and non-empty at the storage boundary immediately
        # before deletion.
        derivative_size = await self._object_size(bucket, derivative_key)
        if derivative_size <= 0:
            raise RuntimeError(f"refusing to delete {raw_key}: derivative {derivative_key} is empty")

        # S3 DeleteObject is idempotent.  If derivative and Whisper completion
        # race and both enter this helper, two deletes are harmless.  Likewise,
        # if a process deleted the object and crashed before the JSONB marker
        # was committed, the reconciler can safely issue DeleteObject again.
        await asyncio.to_thread(self._s3.delete_object, Bucket=bucket, Key=raw_key)

        deleted_at = datetime.now(UTC)
        if not await self._track_repo.mark_raw_pcm_deleted(track_id, deleted_at):
            raise RuntimeError(f"raw PCM was deleted but its DB marker could not be saved: track={track_id}")

        logger.info(
            f"Raw PCM cleanup completed: track={track_id} raw=s3://{bucket}/{raw_key} "
            f"derivative=s3://{bucket}/{derivative_key}"
        )
        return True

    async def start(self) -> None:
        """Start reconciliation for cleanup missed by completion handlers."""
        if self._running or not self._config.enabled:
            return
        self._running = True
        self._reconciler_task = asyncio.create_task(self._reconciler_loop(), name="raw-pcm-cleanup-reconciler")
        logger.info("Raw PCM cleanup reconciler started")

    async def stop(self) -> None:
        """Stop the reconciler without interrupting orchestrator shutdown."""
        self._running = False
        if self._reconciler_task:
            self._reconciler_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reconciler_task
            self._reconciler_task = None
        logger.info("Raw PCM cleanup reconciler stopped")

    async def reconcile_once(self) -> int:
        """Retry every currently eligible PCM and return the success count."""
        candidates = await self._track_repo.list_raw_pcm_cleanup_candidates(limit=RECONCILE_BATCH_SIZE)
        cleaned = 0
        for track in candidates:
            try:
                if await self.maybe_delete_raw_pcm(track.id):
                    cleaned += 1
            except Exception as e:
                # A failure for one object must not prevent the rest of the
                # batch from being attempted.  Leaving raw_deleted_at unset is
                # the retry mechanism: the next pass selects this row again.
                logger.error(f"Raw PCM reconciliation failed for track={track.id}: {e}", exc_info=True)
        return cleaned

    async def _reconciler_loop(self) -> None:
        """Poll forever; derived DB state makes event ordering irrelevant."""
        while self._running:
            try:
                cleaned = await self.reconcile_once()
                if cleaned:
                    logger.info(f"Raw PCM reconciler deleted {cleaned} object(s)")
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # A batch-level database failure must not permanently kill the
                # worker.  The same loop remains alive and retries after its
                # normal interval; no replacement task is spawned, which also
                # keeps stop() and task ownership straightforward.
                logger.error(f"Raw PCM reconciler loop error: {e}", exc_info=True)

            if self._running:
                await asyncio.sleep(RECONCILE_INTERVAL_SECONDS)

    async def _object_size(self, bucket: str, key: str) -> int:
        response = await asyncio.to_thread(self._s3.head_object, Bucket=bucket, Key=key)
        return int(response.get("ContentLength", 0))

    def _bucket_from_location(self, location: str) -> str:
        parsed = urlparse(location)
        if parsed.scheme == "s3" and parsed.netloc:
            return parsed.netloc
        # Current recording events always store s3://bucket/key.  Falling back
        # to configured storage keeps cleanup usable for older rows that only
        # recorded an object key or used a pre-migration location shape.
        return self._config.bucket
