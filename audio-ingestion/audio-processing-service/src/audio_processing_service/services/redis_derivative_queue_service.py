"""
Redis-based Derivative Queue Service.

Adapted from stt_service/service/redis/redis_transcription_queue_service.py
(audio-ingestion PLAN.md D28 point 3 -- same consumer-loop/orphan-recovery
shape, different stream key and task type). Stream key/group name must match
what orchestrator_service's AudioDerivativeService produces to
(Architect_MultiClient_Server/orchestrator_service/services/audio_derivative_service.py).
"""

import asyncio
import logging
import time
from typing import Optional, Callable

from audio_processing_service.models.derivative_task import AudioDerivativeStreamTask
from audio_processing_service.infra.redis.redis_stream_service import (
    RedisStreamService,
    create_stream_service,
)
from audio_processing_service.utils.decorator import singleton

logger = logging.getLogger(__name__)

STREAM_KEY = "audio_derivative:stream"
GROUP_NAME = "audio-processing-workers"

CONSUMER_TASK_NAME = "redis-derivative-consumer"
ORPHAN_RECOVERY_TASK_NAME = "redis-derivative-orphan-recovery"


def get_redis_stream_service() -> 'RedisStreamService[AudioDerivativeStreamTask]':
    return create_stream_service(
        task_class=AudioDerivativeStreamTask, stream_key=STREAM_KEY, group_name=GROUP_NAME
    )


@singleton
class RedisDerivativeQueueService:
    """
    Consumer loop:
    1. XREADGROUP - block waiting for new tasks
    2. Process task with configured processor
    3. XACK on success, reject (retry/DLQ) on failure
    4. Periodically claim orphaned tasks from crashed workers

    Same single-task-at-a-time enforcement as RedisTranscriptionQueueService
    -- audio-processing-service is a CPU-bound transcode worker (PLAN.md
    section 4), so 1 process = 1 concurrent ffmpeg job; scale by running more
    process instances (systemd template unit), not more concurrency per
    process.
    """

    def __init__(self):
        self._redis_service: Optional[RedisStreamService[AudioDerivativeStreamTask]] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._orphan_recovery_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        self._current_task: Optional[AudioDerivativeStreamTask] = None

        self._processing_task_ids: set[str] = set()
        self._processing_lock = asyncio.Lock()

        self._local_stats = {
            "tasks_processed_this_session": 0,
            "tasks_failed_this_session": 0,
            "started_at": None,
        }

        logger.info("RedisDerivativeQueueService initialized")

    def set_processor(self, processor: Callable):
        self._processor = processor
        logger.info("Derivative processor set")

    async def connect(self) -> None:
        if self._redis_service is not None:
            return

        self._redis_service = get_redis_stream_service()
        await self._redis_service.connect()
        logger.info("✅ RedisDerivativeQueueService connected to Redis")

    async def start(self) -> None:
        if self._running:
            logger.warning("RedisDerivativeQueueService already running")
            return

        await self.connect()

        self._running = True
        self._local_stats["started_at"] = time.time()

        await self._redis_service.start_background_tasks()

        self._consumer_task = asyncio.create_task(
            self._consumer_loop(),
            name=CONSUMER_TASK_NAME
        )

        self._orphan_recovery_task = asyncio.create_task(
            self._orphan_recovery_loop(),
            name=ORPHAN_RECOVERY_TASK_NAME
        )

        logger.info(
            f"✅ RedisDerivativeQueueService started\n"
            f"   Consumer ID: {self._redis_service._consumer_id}\n"
            f"   Stream: {self._redis_service._stream_key}\n"
            f"   Group: {self._redis_service._group_name}"
        )

    async def stop(self) -> None:
        logger.info("Stopping RedisDerivativeQueueService...")
        self._running = False

        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass

        if self._orphan_recovery_task:
            self._orphan_recovery_task.cancel()
            try:
                await self._orphan_recovery_task
            except asyncio.CancelledError:
                pass

        if self._redis_service:
            await self._redis_service.stop_background_tasks()
            await self._redis_service.disconnect()

        logger.info("✅ RedisDerivativeQueueService stopped")

    async def _consumer_loop(self) -> None:
        logger.info("🔄 Consumer loop started - waiting for tasks from Redis Stream...")

        while self._running:
            try:
                await self._redis_service.update_heartbeat(current_task_id=None)

                tasks = await self._redis_service.read_tasks(
                    count=1,
                    block_ms=self._redis_service._config.block_timeout_ms
                )

                if not tasks:
                    continue

                for stream_task in tasks:
                    self._current_task = stream_task

                    await self._redis_service.update_heartbeat(
                        current_task_id=stream_task.task_id
                    )

                    success = await self._process_task(stream_task)

                    if success:
                        await self._redis_service.increment_worker_stats(processed=1)
                    else:
                        await self._redis_service.increment_worker_stats(failed=1)

                    self._current_task = None

            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")
                break
            except ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Consumer loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.info("Consumer loop ended")

    async def _process_task(
        self,
        task: AudioDerivativeStreamTask
    ) -> bool:
        # NOTE (PLAN.md D28 point 3): if a task is already being processed and
        # this branch skips a newly-read one, the skipped message is neither
        # ack'd nor rejected -- it stays in the PEL until the next
        # orphan-recovery cycle claims it (~60-90s delay). Self-healing, no
        # data loss. Left unfixed on purpose, same as redis_stream_service.py.
        async with self._processing_lock:
            if self._processing_task_ids:
                logger.warning(
                    f"⚠️ Task {task.task_id} skipped - another task is already being processed "
                    f"(current: {list(self._processing_task_ids)})"
                )
                return False
            self._processing_task_ids.add(task.task_id)

        logger.info(
            f"🎬 Processing derivative task {task.task_id}: track={task.track_id} "
            f"object_key={task.object_key} (retry #{task.retry_count})"
        )

        try:
            if self._processor:
                await self._processor(task)

                await self._redis_service.acknowledge(task)

                self._local_stats["tasks_processed_this_session"] += 1
                logger.info(f"✅ Task {task.task_id} completed")
                return True
            else:
                raise Exception("No processor configured")

        except Exception as e:
            self._local_stats["tasks_failed_this_session"] += 1
            logger.error(f"❌ Task {task.task_id} failed: {e}")

            should_retry = await self._redis_service.reject(
                task,
                error=str(e),
                retry=True
            )

            if should_retry:
                logger.info(f"🔄 Task {task.task_id} will be retried")
            else:
                logger.error(f"Task {task.task_id} moved to dead letter queue")

            return False
        finally:
            async with self._processing_lock:
                self._processing_task_ids.discard(task.task_id)

    async def _orphan_recovery_loop(self) -> None:
        await asyncio.sleep(30)

        claim_interval = self._redis_service._config.worker_timeout_sec

        while self._running:
            try:
                await asyncio.sleep(claim_interval)
                async with self._processing_lock:
                    if self._processing_task_ids:
                        logger.info(
                            f"⚠️ Service is busy processing: {list(self._processing_task_ids)}. "
                            f"Claimed tasks will be processed sequentially when current task completes."
                        )
                        continue
                if not self._running:
                    break

                claimed_tasks = await self._redis_service.claim_orphaned_tasks(count=5)

                if claimed_tasks:
                    logger.info(
                        f"🔄 Claimed {len(claimed_tasks)} orphaned task(s) "
                        f"from crashed workers"
                    )

                    for stream_task in claimed_tasks:
                        stream_task.retry_count += 1
                        self._current_task = stream_task

                        await self._redis_service.update_heartbeat(
                            current_task_id=stream_task.task_id
                        )

                        success = await self._process_task(stream_task)

                        if success:
                            await self._redis_service.increment_worker_stats(processed=1)
                        else:
                            await self._redis_service.increment_worker_stats(failed=1)

                        self._current_task = None

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Orphan recovery error: {e}", exc_info=True)

        logger.info("Orphan recovery loop ended")
