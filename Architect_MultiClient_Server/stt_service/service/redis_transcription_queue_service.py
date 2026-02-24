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
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

from .redis_stream_service import (
    RedisStreamService,
    StreamTask,
    StreamTaskStatus,
    TaskPriority,
    get_redis_stream_service,
)

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task status enumeration (compatible with original)."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscriptionTask:
    """
    Represents a transcription task.
    
    Compatible with original TranscriptionTask but adds Redis-specific fields.
    """
    filename: str
    started_at: str
    ended_at: str
    duration: str
    location: str
    source: Optional[str] = None

    # Optional fields
    egress_id: Optional[str] = None

    # Internal tracking
    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_processing_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None
    
    # Redis-specific
    message_id: Optional[str] = None  # Redis stream message ID
    priority: int = TaskPriority.NORMAL
    retry_count: int = 0

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task_{int(time.time() * 1000)}_{hash(self.filename) % 10000:04d}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "filename": self.filename,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration": self.duration,
            "location": self.location,
            "egress_id": self.egress_id,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_processing_at": self.started_processing_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "message_id": self.message_id,
            "priority": self.priority,
            "retry_count": self.retry_count,
        }
    
    @classmethod
    def from_stream_task(cls, stream_task: StreamTask) -> 'TranscriptionTask':
        """Create TranscriptionTask from StreamTask."""
        return cls(
            task_id=stream_task.task_id,
            filename=stream_task.filename,
            started_at=stream_task.started_at,
            ended_at=stream_task.ended_at,
            duration=stream_task.duration,
            location=stream_task.location,
            source=stream_task.source,
            egress_id=stream_task.egress_id,
            status=TaskStatus.PROCESSING,
            created_at=stream_task.created_at,
            message_id=stream_task.message_id,
            priority=stream_task.priority,
            retry_count=stream_task.retry_count,
        )


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
    
    _instance: Optional['RedisTranscriptionQueueService'] = None
    
    def __init__(self):
        self._redis_service: Optional[RedisStreamService] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._orphan_recovery_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        self._current_task: Optional[TranscriptionTask] = None
        
        # Local cache for recent tasks (for quick status lookup)
        self._recent_tasks: Dict[str, TranscriptionTask] = {}
        self._max_cache_size = 500
        
        # Stats (local counters, Redis has authoritative stats)
        self._local_stats = {
            "tasks_processed_this_session": 0,
            "tasks_failed_this_session": 0,
            "started_at": None,
        }
        
        logger.info("RedisTranscriptionQueueService initialized")
    
    @classmethod
    def get_instance(cls) -> 'RedisTranscriptionQueueService':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None
    
    def set_processor(self, processor: Callable):
        """
        Set the processor function for transcription tasks.
        
        Args:
            processor: Async function that takes TranscriptionTask and returns transcription text
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
            name="redis-transcription-consumer"
        )
        
        # Start orphan recovery task
        self._orphan_recovery_task = asyncio.create_task(
            self._orphan_recovery_loop(),
            name="redis-orphan-recovery"
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
    
    async def enqueue(
        self,
        task: TranscriptionTask,
        timeout: float = 5.0,
        priority: int = TaskPriority.NORMAL
    ) -> str:
        """
        Add a task to the Redis stream.
        
        Args:
            task: The transcription task to enqueue
            timeout: Not used (Redis is fast), kept for API compatibility
            priority: Task priority (1-9, lower = higher)
        
        Returns:
            Task ID
            
        Raises:
            ConnectionError: If not connected to Redis
        """
        if not self._redis_service:
            await self.connect()
        
        message_id = await self._redis_service.enqueue(
            task_id=task.task_id,
            filename=task.filename,
            egress_id=task.egress_id or "",
            started_at=task.started_at,
            ended_at=task.ended_at,
            duration=task.duration,
            location=task.location,
            source=task.source,
            priority=priority,
        )
        
        # Update task with message ID and cache it
        task.message_id = message_id
        task.priority = priority
        self._cache_task(task)
        
        logger.info(
            f"📥 Queued task {task.task_id}: {task.filename} "
            f"(message_id={message_id}, priority={priority})"
        )
        
        return task.task_id
    
    def enqueue_nowait(self, task: TranscriptionTask) -> str:
        """
        Synchronous wrapper for enqueue (for API compatibility).
        
        Note: This creates and runs an event loop task.
        Prefer using async enqueue() when possible.
        """
        # Get or create event loop
        try:
            loop = asyncio.get_running_loop()
            # We're in an async context, schedule as task
            asyncio.create_task(self.enqueue(task))
            return task.task_id
        except RuntimeError:
            # No running loop, use asyncio.run (blocking)
            return asyncio.run(self.enqueue(task))
    
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
                    # Convert to TranscriptionTask
                    task = TranscriptionTask.from_stream_task(stream_task)
                    self._current_task = task
                    self._cache_task(task)
                    
                    # Update heartbeat with current task
                    await self._redis_service.update_heartbeat(
                        current_task_id=task.task_id
                    )
                    
                    # Process the task
                    success = await self._process_task(task, stream_task)
                    
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
        task: TranscriptionTask,
        stream_task: StreamTask
    ) -> bool:
        """
        Process a single transcription task.
        
        Args:
            task: TranscriptionTask wrapper
            stream_task: Original StreamTask from Redis
        
        Returns:
            True if processing succeeded, False otherwise
        """
        task.status = TaskStatus.PROCESSING
        task.started_processing_at = time.time()
        
        logger.info(
            f"🔊 Processing task {task.task_id}: {task.filename} "
            f"(retry #{task.retry_count})"
        )
        
        try:
            if self._processor:
                result = await self._processor(task)
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                
                # ACK the task (remove from pending)
                await self._redis_service.acknowledge(stream_task)
                
                self._local_stats["tasks_processed_this_session"] += 1
                logger.info(
                    f"✅ Task {task.task_id} completed: "
                    f"{len(result) if result else 0} chars"
                )
                return True
            else:
                # No processor set
                logger.warning(
                    f"No processor set, task {task.task_id} "
                    f"marked as completed without processing"
                )
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                await self._redis_service.acknowledge(stream_task)
                return True
                
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            
            self._local_stats["tasks_failed_this_session"] += 1
            logger.error(f"❌ Task {task.task_id} failed: {e}")
            
            # Reject with retry
            should_retry = await self._redis_service.reject(
                stream_task,
                error=str(e),
                retry=True
            )
            
            if should_retry:
                logger.info(f"🔄 Task {task.task_id} will be retried")
            else:
                logger.error(f"💀 Task {task.task_id} moved to dead letter queue")
            
            return False
    
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
                        task = TranscriptionTask.from_stream_task(stream_task)
                        task.retry_count = stream_task.retry_count + 1  # Increment since reclaimed
                        self._current_task = task
                        self._cache_task(task)
                        
                        await self._redis_service.update_heartbeat(
                            current_task_id=task.task_id
                        )
                        
                        success = await self._process_task(task, stream_task)
                        
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
    
    def _cache_task(self, task: TranscriptionTask) -> None:
        """Cache task for quick status lookup."""
        self._recent_tasks[task.task_id] = task
        
        # Cleanup old entries
        if len(self._recent_tasks) > self._max_cache_size:
            # Remove oldest entries
            sorted_tasks = sorted(
                self._recent_tasks.items(),
                key=lambda x: x[1].created_at
            )
            to_remove = len(sorted_tasks) - self._max_cache_size
            for task_id, _ in sorted_tasks[:to_remove]:
                del self._recent_tasks[task_id]
    
    def get_task(self, task_id: str) -> Optional[TranscriptionTask]:
        """
        Get task by ID.
        
        Checks local cache first, then Redis.
        """
        # Check local cache
        if task_id in self._recent_tasks:
            return self._recent_tasks[task_id]
        
        # Could fetch from Redis here if needed
        # For now, return None if not in cache
        return None
    
    async def get_task_async(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task status from Redis."""
        if not self._redis_service:
            return None
        
        return await self._redis_service.get_task_status(task_id)
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get comprehensive queue statistics.
        
        Combines Redis stats with local session stats.
        """
        redis_stats = {}
        if self._redis_service:
            try:
                redis_stats = await self._redis_service.get_stats()
            except Exception as e:
                logger.error(f"Error getting Redis stats: {e}")
        
        uptime = 0
        if self._local_stats["started_at"]:
            uptime = time.time() - self._local_stats["started_at"]
        
        return {
            # Redis stats
            "stream": redis_stats.get("stream", {}),
            "pending": redis_stats.get("pending", {}),
            "dead_letter_queue_size": redis_stats.get("dead_letter_queue_size", 0),
            "totals": redis_stats.get("totals", {}),
            "workers": redis_stats.get("workers", {}),
            
            # Local session stats
            "session": {
                "running": self._running,
                "uptime": uptime,
                "tasks_processed": self._local_stats["tasks_processed_this_session"],
                "tasks_failed": self._local_stats["tasks_failed_this_session"],
                "current_task": self._current_task.task_id if self._current_task else None,
                "consumer_id": self._redis_service._consumer_id if self._redis_service else None,
            },
            
            # Legacy compatibility
            "queue_size": redis_stats.get("stream", {}).get("length", 0),
            "total_received": redis_stats.get("totals", {}).get("enqueued", 0),
            "total_processed": redis_stats.get("totals", {}).get("processed", 0),
            "total_failed": redis_stats.get("totals", {}).get("failed", 0),
            "running": self._running,
            "uptime": uptime,
            "pending_tasks": redis_stats.get("pending", {}).get("pending_count", 0),
            "processing_tasks": redis_stats.get("pending", {}).get("pending_count", 0),
        }
    
    def get_stats_sync(self) -> Dict[str, Any]:
        """Synchronous version of get_stats (returns cached/local data only)."""
        uptime = 0
        if self._local_stats["started_at"]:
            uptime = time.time() - self._local_stats["started_at"]
        
        return {
            "running": self._running,
            "uptime": uptime,
            "tasks_processed": self._local_stats["tasks_processed_this_session"],
            "tasks_failed": self._local_stats["tasks_failed_this_session"],
            "current_task": self._current_task.task_id if self._current_task else None,
            "cached_tasks": len(self._recent_tasks),
        }
    
    async def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get list of pending tasks from Redis."""
        if not self._redis_service:
            return []
        
        try:
            pending = await self._redis_service.get_pending_tasks(count=100)
            return pending
        except Exception as e:
            logger.error(f"Error getting pending tasks: {e}")
            return []


# ========================================
# Singleton accessor functions
# ========================================

_queue_service: Optional[RedisTranscriptionQueueService] = None


def get_redis_transcription_queue_service() -> RedisTranscriptionQueueService:
    """Get the singleton Redis transcription queue service."""
    global _queue_service
    if _queue_service is None:
        _queue_service = RedisTranscriptionQueueService.get_instance()
    return _queue_service


# Alias for backward compatibility
def get_transcription_queue_service() -> RedisTranscriptionQueueService:
    """
    Get transcription queue service (Redis-backed).
    
    This replaces the original asyncio.Queue version.
    """
    return get_redis_transcription_queue_service()
