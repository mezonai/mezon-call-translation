import json
import logging
import time
from enum import StrEnum
from typing import Any, Dict, Optional

from non_realtime_stt_service.service.redis.connection_pool import get_connection_manager
from non_realtime_stt_service.utils.decorator import singleton

logger = logging.getLogger(__name__)

# TTL for async job records in Redis (24 hours)
JOB_TTL_SECONDS: int = 86400


class JobStatus(StrEnum):
    """Lifecycle states of an asynchronous transcription job."""
    QUEUED = "queued"
    INPROGRESS = "inprogress"
    DONE = "done"
    FAILED = "failed"


@singleton
class AsyncJobService:
    """
    Manages asynchronous STT job states and results persisted in Redis.
    Provides persistence and state tracking across distributed workers.
    """

    def __init__(self):
        self._connection_manager = get_connection_manager()
        self._prefix = "stt:job:"

    def _get_job_key(self, job_id: str) -> str:
        """Construct Redis key for a given job ID."""
        return f"{self._prefix}{job_id}"

    async def create_job(self, job_id: str, audio_source: str = "upload") -> Dict[str, Any]:
        """
        Register a new transcription job with initial 'queued' status.

        Args:
            job_id: Unique UUID identifier for the job.
            audio_source: Description or URI of the input audio source.

        Returns:
            Dict containing the initial job record.
        """
        now = time.time()
        job_data: Dict[str, Any] = {
            "job_id": job_id,
            "status": JobStatus.QUEUED.value,
            "audio_source": audio_source,
            "created_at": now,
            "updated_at": now,
            "duration": 0.0,
            "processing_time": 0.0,
            "result": None,
            "error_message": "",
        }

        try:
            client = self._connection_manager.get_client()
            key = self._get_job_key(job_id)
            await client.set(key, json.dumps(job_data), ex=JOB_TTL_SECONDS)
            logger.info("📋 Registered async STT job: %s (status: %s)", job_id, JobStatus.QUEUED.value)
        except Exception as e:
            logger.error("Failed to register job %s in Redis: %s", job_id, e, exc_info=True)

        return job_data

    async def update_status(
        self,
        job_id: str,
        status: JobStatus | str,
        result: Optional[Dict[str, Any]] = None,
        error_message: str = "",
        duration: Optional[float] = None,
        processing_time: Optional[float] = None,
    ) -> None:
        """
        Update the progress status, metrics, and result of an existing job.

        Args:
            job_id: Unique job identifier.
            status: Target state ('inprogress', 'done', 'failed').
            result: Transcription payload containing text and segments (when done).
            error_message: Error description (when failed).
            duration: Total audio duration in seconds.
            processing_time: Actual GPU/model inference time in seconds.
        """
        status_value = status.value if isinstance(status, JobStatus) else str(status)

        try:
            client = self._connection_manager.get_client()
            key = self._get_job_key(job_id)
            raw = await client.get(key)

            if raw:
                raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")
                job_data = json.loads(raw_str)
            else:
                job_data = {"job_id": job_id, "created_at": time.time()}

            job_data["status"] = status_value
            job_data["updated_at"] = time.time()

            if result is not None:
                job_data["result"] = result
            if error_message:
                job_data["error_message"] = error_message
            if duration is not None:
                job_data["duration"] = round(duration, 3)
            if processing_time is not None:
                job_data["processing_time"] = round(processing_time, 3)

            await client.set(key, json.dumps(job_data), ex=JOB_TTL_SECONDS)
            logger.info("🔄 Job state transitioned: %s -> %s", job_id, status_value)

        except Exception as e:
            logger.error("Failed to update status for job %s: %s", job_id, e, exc_info=True)

    async def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve complete job record from Redis.

        Args:
            job_id: Unique job identifier.

        Returns:
            Dict of job metadata, or None if not found/expired.
        """
        try:
            client = self._connection_manager.get_client()
            key = self._get_job_key(job_id)
            raw = await client.get(key)
            if not raw:
                return None
            raw_str = raw if isinstance(raw, str) else raw.decode("utf-8")
            return json.loads(raw_str)
        except Exception as e:
            logger.error("Failed to fetch job %s from Redis: %s", job_id, e, exc_info=True)
            return None


def get_async_job_service() -> AsyncJobService:
    """Convenience getter for singleton AsyncJobService instance."""
    return AsyncJobService()
