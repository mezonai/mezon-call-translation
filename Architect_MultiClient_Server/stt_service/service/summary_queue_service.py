"""
Summary Queue Service

Queue-based service for processing room summaries asynchronously.
Uses asyncio.Queue for non-blocking, event-driven task processing.
"""

import asyncio
import logging
import time
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class SummaryTaskStatus(str, Enum):
    """Summary task status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class SummaryTask:
    """Represents a summary generation task for a room."""
    room_id: str
    trigger_time: float = field(default_factory=time.time)
    
    # Internal tracking
    task_id: str = ""
    status: SummaryTaskStatus = SummaryTaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_processing_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    
    # Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.task_id:
            # Generate unique task ID
            self.task_id = f"summary_{int(time.time() * 1000)}_{hash(self.room_id) % 10000:04d}"

    def to_dict(self) -> Dict[str, Any]:
        """Convert task to dictionary."""
        return {
            "task_id": self.task_id,
            "room_id": self.room_id,
            "trigger_time": self.trigger_time,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_processing_at": self.started_processing_at,
            "completed_at": self.completed_at,
            "error": self.error,
            "metadata": self.metadata,
        }


class SummaryQueueService:
    """
    Async queue service for summary generation tasks.
    
    Features:
    - Event-driven: consumer awaits get(), no polling
    - Non-blocking: doesn't block other async operations
    - Automatic retry on failure
    - Task tracking and statistics
    """
    
    _instance: Optional['SummaryQueueService'] = None
    
    def __init__(self, maxsize: int = 500):
        """
        Initialize summary queue service.
        
        Args:
            maxsize: Maximum queue size (default: 500, less than transcription queue)
        """
        self._queue: asyncio.Queue[SummaryTask] = asyncio.Queue(maxsize=maxsize)
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        
        # Task tracking
        self._tasks: Dict[str, SummaryTask] = {}
        self._max_history = 500  # Keep last N completed tasks
        
        # Stats
        self._stats = {
            "total_received": 0,
            "total_processed": 0,
            "total_failed": 0,
            "started_at": None,
        }
        
        logger.info(f"SummaryQueueService initialized with maxsize={maxsize}")
    
    @classmethod
    def get_instance(cls, maxsize: int = 500) -> 'SummaryQueueService':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls(maxsize=maxsize)
        return cls._instance
    
    def set_processor(self, processor: Callable):
        """
        Set the processor function for summary tasks.
        
        Args:
            processor: Async function that takes room_id and returns summary result
        """
        self._processor = processor
        logger.info("Summary processor set")
    
    async def start(self):
        """Start the consumer task."""
        if self._running:
            logger.warning("SummaryQueueService already running")
            return
        
        self._running = True
        self._stats["started_at"] = time.time()
        self._consumer_task = asyncio.create_task(self._consumer_loop())
        logger.info("✅ SummaryQueueService started - consumer is waiting for tasks")
    
    async def stop(self):
        """Stop the consumer task gracefully."""
        self._running = False
        
        if self._consumer_task:
            # Put sentinel to wake up consumer
            try:
                self._queue.put_nowait(None)  # type: ignore
            except asyncio.QueueFull:
                pass
            
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
            
        logger.info("SummaryQueueService stopped")
    
    async def enqueue_summary(self, room_id: str, timeout: float = 5.0, **metadata) -> str:
        """
        Add a summary task to the queue.
        
        Args:
            room_id: Room ID to generate summary for
            timeout: Maximum seconds to wait if queue is full (default: 5s)
            **metadata: Additional metadata for the task
        
        Returns:
            Task ID
            
        Raises:
            asyncio.TimeoutError: If queue is full and timeout expires
        """
        task = SummaryTask(room_id=room_id, metadata=metadata)
        
        try:
            await asyncio.wait_for(self._queue.put(task), timeout=timeout)
            self._tasks[task.task_id] = task
            self._stats["total_received"] += 1
            
            logger.info(
                f"📥 Queued summary task {task.task_id} for room {room_id} "
                f"(queue size: {self._queue.qsize()})"
            )
            return task.task_id
        except asyncio.TimeoutError:
            logger.error(
                f"⚠️ Summary queue full! Failed to enqueue task for room {room_id} "
                f"after {timeout}s. Queue size: {self._queue.qsize()}/{self._queue.maxsize}"
            )
            raise
    
    def enqueue_nowait(self, room_id: str, **metadata) -> str:
        """
        Add a summary task to the queue without waiting.
        
        Args:
            room_id: Room ID to generate summary for
            **metadata: Additional metadata for the task
        
        Returns:
            Task ID
            
        Raises:
            asyncio.QueueFull: If queue is full
        """
        task = SummaryTask(room_id=room_id, metadata=metadata)
        
        try:
            self._queue.put_nowait(task)
            self._tasks[task.task_id] = task
            self._stats["total_received"] += 1
            
            logger.info(
                f"📥 Queued summary task {task.task_id} for room {room_id} "
                f"(queue size: {self._queue.qsize()})"
            )
            return task.task_id
        except asyncio.QueueFull:
            logger.warning(
                f"⚠️ Summary queue full! Cannot enqueue task for room {room_id}. "
                f"Queue size: {self._queue.qsize()}/{self._queue.maxsize}"
            )
            raise
    
    async def _consumer_loop(self):
        """
        Main consumer loop for processing summary tasks.
        
        Awaits for tasks in queue - NO POLLING, event-driven.
        """
        logger.info("🔄 Summary consumer loop started - waiting for tasks...")
        
        while self._running:
            try:
                # Event-driven: awaits until item is available
                task = await self._queue.get()
                
                # Check for sentinel (shutdown signal)
                if task is None:
                    logger.info("Summary consumer received shutdown signal")
                    break
                
                await self._process_task(task)
                self._queue.task_done()
                
            except asyncio.CancelledError:
                logger.info("Summary consumer loop cancelled")
                break
            except Exception as e:
                logger.error(f"Summary consumer loop error: {e}", exc_info=True)
                logger.warning(f"Queue size: {self._queue.qsize()}, Retrying in 2 seconds...")
                await asyncio.sleep(2)  # Longer delay for summaries
    
    async def _process_task(self, task: SummaryTask):
        """
        Process a single summary task.
        
        Args:
            task: Summary task to process
        """
        task.status = SummaryTaskStatus.PROCESSING
        task.started_processing_at = time.time()
        
        logger.info(f"📝 Processing summary task {task.task_id} for room {task.room_id}")
        
        try:
            if not self._processor:
                raise RuntimeError("No processor set for summary queue")
            
            # Call the processor (summary_processor.process_summary)
            result = await self._processor(task.room_id)
            
            if result:
                task.result = result
                task.status = SummaryTaskStatus.COMPLETED
                self._stats["total_processed"] += 1
                
                summary_id = result.get("_id", "N/A")
                logger.info(
                    f"✅ Summary task {task.task_id} completed successfully "
                    f"(summary_id: {summary_id})"
                )
            else:
                raise RuntimeError("Processor returned None or empty result")
                
        except Exception as e:
            task.status = SummaryTaskStatus.FAILED
            task.error = str(e)
            self._stats["total_failed"] += 1
            logger.error(
                f"❌ Summary task {task.task_id} failed for room {task.room_id}: {e}",
                exc_info=True
            )
        
        finally:
            task.completed_at = time.time()
            
            # Log processing time
            processing_time = task.completed_at - task.started_processing_at
            logger.info(
                f"Summary task {task.task_id} finished in {processing_time:.2f}s "
                f"(status: {task.status.value})"
            )
            
            self._cleanup_old_tasks()
    
    def _cleanup_old_tasks(self):
        """Remove old completed tasks to prevent memory growth."""
        completed = [
            (t.completed_at, tid)
            for tid, t in self._tasks.items()
            if t.status in (SummaryTaskStatus.COMPLETED, SummaryTaskStatus.FAILED) 
            and t.completed_at
        ]
        
        if len(completed) > self._max_history:
            completed.sort()
            to_remove = len(completed) - self._max_history
            for _, tid in completed[:to_remove]:
                del self._tasks[tid]
            
            logger.debug(f"Cleaned up {to_remove} old summary tasks")
    
    def get_task(self, task_id: str) -> Optional[SummaryTask]:
        """
        Get task by ID.
        
        Args:
            task_id: Task identifier
            
        Returns:
            SummaryTask if found, None otherwise
        """
        return self._tasks.get(task_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get queue statistics.
        
        Returns:
            Dictionary with queue statistics
        """
        uptime = 0
        if self._stats["started_at"]:
            uptime = time.time() - self._stats["started_at"]
        
        return {
            "queue_size": self._queue.qsize(),
            "total_received": self._stats["total_received"],
            "total_processed": self._stats["total_processed"],
            "total_failed": self._stats["total_failed"],
            "success_rate": (
                self._stats["total_processed"] / self._stats["total_received"] 
                if self._stats["total_received"] > 0 
                else 0.0
            ),
            "running": self._running,
            "uptime": uptime,
            "pending_tasks": len([
                t for t in self._tasks.values() 
                if t.status == SummaryTaskStatus.PENDING
            ]),
            "processing_tasks": len([
                t for t in self._tasks.values() 
                if t.status == SummaryTaskStatus.PROCESSING
            ]),
        }
    
    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """
        Get list of pending and processing tasks.
        
        Returns:
            List of task dictionaries
        """
        return [
            t.to_dict() for t in self._tasks.values()
            if t.status in (SummaryTaskStatus.PENDING, SummaryTaskStatus.PROCESSING)
        ]


def get_summary_queue_service(maxsize: int = 500) -> SummaryQueueService:
    """
    Get the singleton summary queue service.
    
    Args:
        maxsize: Maximum queue size
        
    Returns:
        SummaryQueueService instance
    """
    return SummaryQueueService.get_instance(maxsize)
