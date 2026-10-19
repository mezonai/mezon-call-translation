"""
Redis-based Transcription Queue Service

Replaces asyncio.Queue with Redis Streams for:
- Persistent task storage (survives crashes)
- Distributed workers (multiple consumers)
- Automatic recovery of orphaned tasks
- Priority queue support
"""

import asyncio
import logging
import time
from typing import Optional, Callable

from non_realtime_stt_service.models.transcription_task import TranscriptionStreamTask
from non_realtime_stt_service.models.stream_base import StreamTaskStatus
from non_realtime_stt_service.service.redis.redis_stream_service import (
    RedisStreamService,
    create_stream_service,
)
from non_realtime_stt_service.utils.decorator import singleton

logger = logging.getLogger(__name__)
stream_key = "transcription:stream"
group_name = "transcription-workers"

CONSUMER_TASK_NAME = "redis-transcription-consumer"
ORPHAN_RECOVERY_TASK_NAME = "redis-orphan-recovery"

# ========================================
# Transcription-specific factory function
# ========================================

def get_redis_stream_service() -> 'RedisStreamService[TranscriptionStreamTask]':
    """
    Get singleton RedisStreamService for transcription tasks.
    
    Convenience function for transcription-specific usage.
    
    Returns:
        RedisStreamService[TranscriptionStreamTask] singleton instance
    """
    return create_stream_service(task_class=TranscriptionStreamTask, stream_key=stream_key, group_name=group_name)


@singleton
class RedisTranscriptionQueueService:
    """
    Redis Stream-based transcription queue service.
    
    Key differences from asyncio.Queue version:
    - Tasks persist in Redis (survive crashes)
    - Multiple workers can process tasks (consumer groups)
    - Orphaned tasks auto-recovered after timeout
    - Built-in retry mechanism with dead letter queue
    
    Consumer Loop:
    1. XREADGROUP - block waiting for new tasks
    2. Process task with configured processor
    3. XACK on success, reject (retry/DLQ) on failure
    4. Periodically claim orphaned tasks from crashed workers
    """
    
    def __init__(self):
        self._redis_service: Optional[RedisStreamService[TranscriptionStreamTask]] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._orphan_recovery_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        self._current_task: Optional[TranscriptionStreamTask] = None
        
        # Lock to prevent double processing of same task
        self._processing_task_ids: set[str] = set()  # Track tasks currently being processed
        self._processing_lock = asyncio.Lock()
        
        # Stats (local counters, Redis has authoritative stats)
        self._local_stats = {
            "tasks_processed_this_session": 0,
            "tasks_failed_this_session": 0,
            "started_at": None,
        }
        
        logger.info("RedisTranscriptionQueueService initialized")
    
    def set_processor(self, processor: Callable):
        """
        Set the processor function for transcription tasks.
        
        Args:
            processor: Async function that takes TranscriptionStreamTask and returns transcription text
        """
        self._processor = processor
        logger.info("Transcription processor set")
    
    async def connect(self) -> None:
        """
        Connect to Redis and initialize.
        
        Call this before start() or enqueue().
        """
        if self._redis_service is not None:
            return
        
        self._redis_service = get_redis_stream_service()
        await self._redis_service.connect()
        logger.info("✅ RedisTranscriptionQueueService connected to Redis")
    
    async def start(self) -> None:
        """
        Start the consumer loop and background tasks.
        
        This will:
        1. Connect to Redis (if not already)
        2. Start heartbeat & worker registration
        3. Start consumer loop (XREADGROUP)
        4. Start orphan recovery task
        """
        if self._running:
            logger.warning("RedisTranscriptionQueueService already running")
            return
        
        # Ensure connected
        await self.connect()
        
        self._running = True
        self._local_stats["started_at"] = time.time()
        
        # Start Redis background tasks (heartbeat)
        await self._redis_service.start_background_tasks()
        
        # Start consumer loop
        self._consumer_task = asyncio.create_task(
            self._consumer_loop(),
            name=CONSUMER_TASK_NAME
        )
        
        # Start orphan recovery task
        self._orphan_recovery_task = asyncio.create_task(
            self._orphan_recovery_loop(),
            name=ORPHAN_RECOVERY_TASK_NAME
        )
        
        logger.info(
            f"✅ RedisTranscriptionQueueService started\n"
            f"   Consumer ID: {self._redis_service._consumer_id}\n"
            f"   Stream: {self._redis_service._stream_key}\n"
            f"   Group: {self._redis_service._group_name}"
        )
    
    async def stop(self) -> None:
        """
        Stop the service gracefully.
        
        Current processing task will complete before shutdown.
        """
        logger.info("Stopping RedisTranscriptionQueueService...")
        self._running = False
        
        # Cancel consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        
        # Cancel orphan recovery task
        if self._orphan_recovery_task:
            self._orphan_recovery_task.cancel()
            try:
                await self._orphan_recovery_task
            except asyncio.CancelledError:
                pass
        
        # Stop Redis background tasks
        if self._redis_service:
            await self._redis_service.stop_background_tasks()
            await self._redis_service.disconnect()
        
        logger.info("✅ RedisTranscriptionQueueService stopped")
    
    async def _consumer_loop(self) -> None:
        """
        Main consumer loop using Redis XREADGROUP.
        
        Workflow:
        1. XREADGROUP blocks until a task is available
        2. Process task with configured processor
        3. On success: XACK (remove from pending)
        4. On failure: reject() (retry or DLQ)
        5. Update heartbeat with current task info
        """
        logger.info("🔄 Consumer loop started - waiting for tasks from Redis Stream...")
        
        while self._running:
            try:
                # Update heartbeat with "idle" status
                await self._redis_service.update_heartbeat(current_task_id=None)
                
                # XREADGROUP - blocks until task available
                # This is event-driven, no polling!
                tasks = await self._redis_service.read_tasks(
                    count=1,  # Process one task at a time
                    block_ms=self._redis_service._config.block_timeout_ms
                )
                
                if not tasks:
                    # Timeout reached, loop continues (check _running flag)
                    continue
                
                for stream_task in tasks:
                    # Use stream_task directly (no conversion needed)
                    self._current_task = stream_task
                    
                    # Update heartbeat with current task
                    await self._redis_service.update_heartbeat(
                        current_task_id=stream_task.task_id
                    )
                    
                    # Process the task
                    success = await self._process_task(stream_task)
                    
                    # Update worker stats
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
                await asyncio.sleep(5)  # Wait before retry
            except Exception as e:
                logger.error(f"Consumer loop error: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        logger.info("Consumer loop ended")
    
    async def _process_task(
        self,
        task: TranscriptionStreamTask
    ) -> bool:
        """
        Process a single transcription task with single-task-at-a-time enforcement.
        Ensures only ONE task is processed at a time in this service instance.
        
        Args:
            task: TranscriptionStreamTask from Redis
        
        Returns:
            True if processing succeeded, False otherwise or if another task is being processed
        """
        # Check if ANY task is currently being processed (enforce single-task processing)
        async with self._processing_lock:
            if self._processing_task_ids:
                logger.warning(
                    f"⚠️ Task {task.task_id} skipped - another task is already being processed "
                    f"(current: {list(self._processing_task_ids)})"
                )
                return False
            self._processing_task_ids.add(task.task_id)
        
        task.status = StreamTaskStatus.PROCESSING
        task.started_processing_at = time.time()
        
        logger.info(
            f"🔊 Processing task {task.task_id}: {task.filename} "
            f"(retry #{task.retry_count})"
        )
        
        try:
            if self._processor:
                result = await self._processor(task)
                task.result = result
                task.status = StreamTaskStatus.COMPLETED
                task.completed_at = time.time()
                
                # ACK the task (remove from pending)
                await self._redis_service.acknowledge(task)
                
                self._local_stats["tasks_processed_this_session"] += 1
                logger.info(
                    f"✅ Task {task.task_id} completed: "
                    f"{len(result) if result else 0} chars"
                )
                return True
            else:
                raise Exception("No processor configured")
                
        except Exception as e:
            task.status = StreamTaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            
            self._local_stats["tasks_failed_this_session"] += 1
            logger.error(f"❌ Task {task.task_id} failed: {e}")
            
            # Reject with retry
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
            # Always remove from processing set
            async with self._processing_lock:
                self._processing_task_ids.discard(task.task_id)
    
    async def _orphan_recovery_loop(self) -> None:
        """
        Periodically check for and claim orphaned tasks.
        
        Orphaned tasks are those being processed by workers that crashed
        or stopped responding (no heartbeat).
        
        Uses XAUTOCLAIM to atomically claim these tasks.
        """
        # Wait a bit before first check
        await asyncio.sleep(30)
        
        claim_interval = self._redis_service._config.worker_timeout_sec
        
        while self._running:
            try:
                await asyncio.sleep(claim_interval)
                # Check if any task is currently being processed
                async with self._processing_lock:
                    if self._processing_task_ids:
                        logger.info(
                            f"⚠️ Service is busy processing: {list(self._processing_task_ids)}. "
                            f"Claimed tasks will be processed sequentially when current task completes."
                        )
                        continue
                if not self._running:
                    break
                
                # Try to claim orphaned tasks
                claimed_tasks = await self._redis_service.claim_orphaned_tasks(
                    count=5  # Claim up to 5 at a time
                )
                
                if claimed_tasks:
                    logger.info(
                        f"🔄 Claimed {len(claimed_tasks)} orphaned task(s) "
                        f"from crashed workers"
                    )
                    
                    # Process claimed tasks
                    for stream_task in claimed_tasks:
                        # Use stream_task directly, increment retry_count
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
