"""Delete raw PCM after both of its independent consumers have finished.

`recording.completed` starts Whisper transcription and OGG derivative creation
in parallel.  Deleting from either completion handler unconditionally is
therefore unsafe: the other worker may not have downloaded the PCM yet.

This service uses the two existing Track statuses as a synchronization barrier:

    status == "completed"             (Whisper finished)
    derivative_status == "completed"  (OGG uploaded)

"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig

from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.postgresql.pg_track_repository import PgTrackRepository
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

@singleton
class RawPcmCleanupService:
    def __init__(self):
        self._config = get_config().minio
        self._track_repo = PgTrackRepository()
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
        call, and False if erroneous. Still need future cleanup mechanism for failed cases.
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

        # checking .ogg file actually exists in MinIO and is not empty before deleting the original .pcm:
        derivative_size = await self._object_size(bucket, derivative_key)
        if derivative_size <= 0:
            raise RuntimeError(f"refusing to delete {raw_key}: derivative {derivative_key} is empty")

        # S3 DeleteObject is idempotent.  If derivative and Whisper completion
        # race and both enter this helper, two deletes are harmless.
        await asyncio.to_thread(self._s3.delete_object, Bucket=bucket, Key=raw_key)

        deleted_at = datetime.now(UTC)
        if not await self._track_repo.mark_raw_pcm_deleted(track_id, deleted_at):
            raise RuntimeError(f"raw PCM was deleted but its DB marker could not be saved: track={track_id}")

        logger.info(
            f"Raw PCM cleanup completed: track={track_id} raw=s3://{bucket}/{raw_key} "
            f"derivative=s3://{bucket}/{derivative_key}"
        )
        return True


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
