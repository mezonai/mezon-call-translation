"""
Audio Derivative Service - dispatches "transcode raw capture" jobs to
audio-processing-service via Redis Stream (audio-ingestion PLAN.md D17).

Mirrors TranscriptionService's lazy Redis producer pattern exactly --
same factory, same connect-on-first-use, different stream key/task type.
audio-processing-service (Phase 5) is the consumer; this service only
produces.
"""

from typing import Optional

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.models.audio_derivative_task import AudioDerivativeTask
from orchestrator_service.services.redis.redis_producer_service import (
    RedisProducerService,
    create_producer_service,
)

logger = get_logger(__name__)


class AudioDerivativeService:
    """Producer-only service for the audio_derivative:stream Redis Stream."""

    def __init__(self):
        self._redis_producer: Optional[RedisProducerService[AudioDerivativeTask]] = None
        self.stream_key = "audio_derivative:stream"

    async def _get_producer(self) -> RedisProducerService[AudioDerivativeTask]:
        if not self._redis_producer:
            self._redis_producer = create_producer_service(
                task_class=AudioDerivativeTask,
                stream_key=self.stream_key,
            )
            await self._redis_producer.connect()
        return self._redis_producer

    async def enqueue(
        self,
        *,
        track_id: str,
        room_id: str,
        bucket: str,
        object_key: str,
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> bool:
        try:
            producer = await self._get_producer()
            task = AudioDerivativeTask(
                track_id=track_id,
                room_id=room_id,
                bucket=bucket,
                object_key=object_key,
                sample_rate=sample_rate,
                channels=channels,
            )
            task_id = await producer.enqueue(task)
            logger.info(f"✓ Queued for derivative transcode: {track_id} → {task_id}")
            return True
        except Exception as e:
            logger.error(f"✗ audio_derivative:stream enqueue failed: {e}")
            return False


_audio_derivative_service: Optional[AudioDerivativeService] = None


def get_audio_derivative_service() -> AudioDerivativeService:
    global _audio_derivative_service
    if _audio_derivative_service is None:
        _audio_derivative_service = AudioDerivativeService()
    return _audio_derivative_service
