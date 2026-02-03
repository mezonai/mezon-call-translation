"""
Transcription Queue Service

Uses asyncio.Queue for non-blocking, event-driven task processing.
The consumer awaits queue.get() so it doesn't poll - it waits efficiently
for new items without CPU waste or blocking other coroutines.
"""

import asyncio
import logging
import time
import aiohttp
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from bson import ObjectId

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """Task status enumeration."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class TranscriptionTask:
    """Represents a transcription task from MinIO/S3."""
    filename: str
    started_at: str
    ended_at: str
    duration: str
    size: str
    location: str

    # New fields for richer context
    egress_id: Optional[str] = None
    room_id: ObjectId = None
    participant_identity: Optional[str] = None
    track_id: Optional[str] = None
    track_type: Optional[str] = None
    track_source: Optional[str] = None

    # Internal tracking
    task_id: str = ""
    status: TaskStatus = TaskStatus.PENDING
    created_at: float = field(default_factory=time.time)
    started_processing_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None

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
            "size": self.size,
            "location": self.location,
            "egress_id": self.egress_id,
            "room_id": self.room_id,
            "participant_identity": self.participant_identity,
            "track_id": self.track_id,
            "track_type": self.track_type,
            "track_source": self.track_source,
            "status": self.status.value,
            "created_at": self.created_at,
            "started_processing_at": self.started_processing_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
        }


class TranscriptionQueueService:
    """
    Async queue service for transcription tasks.
    
    Uses asyncio.Queue which is:
    - Event-driven: consumer awaits get(), no polling
    - Non-blocking: doesn't block other async operations
    - Memory efficient: built-in backpressure with maxsize
    """
    
    _instance: Optional['TranscriptionQueueService'] = None
    
    def __init__(self, maxsize: int = 1000):
        self._queue: asyncio.Queue[TranscriptionTask] = asyncio.Queue(maxsize=maxsize)
        self._consumer_task: Optional[asyncio.Task] = None
        self._running = False
        self._processor: Optional[Callable] = None
        self.mongodb_service = None
        # Task tracking
        self._tasks: Dict[str, TranscriptionTask] = {}
        self._max_history = 1000  # Keep last N completed tasks
        
        # Stats
        self._stats = {
            "total_received": 0,
            "total_processed": 0,
            "total_failed": 0,
            "started_at": None,
        }
        
        logger.info(f"TranscriptionQueueService initialized with maxsize={maxsize}")
    
    @classmethod
    def get_instance(cls, maxsize: int = 1000) -> 'TranscriptionQueueService':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls(maxsize=maxsize)
        return cls._instance
    
    def set_processor(self, processor: Callable):
        """
        Set the processor function for transcription tasks.
        
        Args:
            processor: Async function that takes TranscriptionTask and returns transcription text
        """
        self._processor = processor
        logger.info("Transcription processor set")
    
    async def start(self):
        """Start the consumer task."""
        if self._running:
            logger.warning("TranscriptionQueueService already running")
            return
        
        self._running = True
        self._stats["started_at"] = time.time()
        self._consumer_task = asyncio.create_task(self._consumer_loop())
        logger.info("✅ TranscriptionQueueService started - consumer is waiting for tasks")
    
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
            
        logger.info("TranscriptionQueueService stopped")
    
    async def enqueue(self, task: TranscriptionTask) -> str:
        """
        Add a task to the queue.
        
        Non-blocking if queue has space, waits if full (backpressure).
        
        Returns:
            Task ID
        """
        await self._queue.put(task)
        self._tasks[task.task_id] = task
        self._stats["total_received"] += 1
        
        logger.info(f"📥 Queued task {task.task_id}: {task.filename} (queue size: {self._queue.qsize()})")
        return task.task_id
    
    def enqueue_nowait(self, task: TranscriptionTask) -> str:
        """
        Add a task to the queue without waiting.
        
        Raises:
            asyncio.QueueFull: If queue is full
            
        Returns:
            Task ID
        """
        self._queue.put_nowait(task)
        self._tasks[task.task_id] = task
        self._stats["total_received"] += 1
        
        logger.info(f"📥 Queued task {task.task_id}: {task.filename} (queue size: {self._queue.qsize()})")
        return task.task_id
    
    async def _consumer_loop(self):
        """
        Main consumer loop.
        
        await queue.get() is event-driven - it suspends until an item is available.
        This means NO POLLING, NO CPU WASTE, and doesn't block other coroutines.
        """
        logger.info("🔄 Consumer loop started - waiting for tasks...")
        
        while self._running:
            try:
                # This AWAITS until an item is available - NO POLLING!
                task = await self._queue.get()
                
                # Check for sentinel (shutdown signal)
                if task is None:
                    logger.info("Consumer received shutdown signal")
                    break
                
                await self._process_task(task)
                self._queue.task_done()
                
            except asyncio.CancelledError:
                logger.info("Consumer loop cancelled")
                break
            except Exception as e:
                logger.error(f"Consumer loop error: {e}", exc_info=True)
                await asyncio.sleep(100)  # Prevent tight loop on repeated errors
    
    async def _process_task(self, task: TranscriptionTask):
        """Process a single transcription task."""
        task.status = TaskStatus.PROCESSING
        task.started_processing_at = time.time()
        
        logger.info(f"🔊 Processing task {task.task_id}: {task.filename}")
        
        try:
            if self._processor:
                result = await self._processor(task)
                task.result = result
                task.status = TaskStatus.COMPLETED
                self._stats["total_processed"] += 1
                logger.info(f"✅ Task {task.task_id} completed: {len(result) if result else 0} chars")
            else:
                # Default: just log (no processor set)
                logger.warning(f"No processor set, task {task.task_id} marked as completed without processing")
                task.status = TaskStatus.COMPLETED
                self._stats["total_processed"] += 1
                
        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            self._stats["total_failed"] += 1
            logger.error(f"❌ Task {task.task_id} failed: {e}")
        
        finally:
            task.completed_at = time.time()
            self._cleanup_old_tasks()
    
    def _cleanup_old_tasks(self):
        """Remove old completed tasks to prevent memory growth."""
        completed = [
            (t.completed_at, tid)
            for tid, t in self._tasks.items()
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED) and t.completed_at
        ]
        
        if len(completed) > self._max_history:
            completed.sort()
            to_remove = len(completed) - self._max_history
            for _, tid in completed[:to_remove]:
                del self._tasks[tid]
    
    def get_task(self, task_id: str) -> Optional[TranscriptionTask]:
        """Get task by ID."""
        return self._tasks.get(task_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        return {
            "queue_size": self._queue.qsize(),
            "total_received": self._stats["total_received"],
            "total_processed": self._stats["total_processed"],
            "total_failed": self._stats["total_failed"],
            "running": self._running,
            "uptime": time.time() - self._stats["started_at"] if self._stats["started_at"] else 0,
            "pending_tasks": len([t for t in self._tasks.values() if t.status == TaskStatus.PENDING]),
            "processing_tasks": len([t for t in self._tasks.values() if t.status == TaskStatus.PROCESSING]),
        }
    
    def get_pending_tasks(self) -> List[Dict[str, Any]]:
        """Get list of pending tasks."""
        return [
            t.to_dict() for t in self._tasks.values()
            if t.status in (TaskStatus.PENDING, TaskStatus.PROCESSING)
        ]


def get_transcription_queue_service(maxsize: int = 1000) -> TranscriptionQueueService:
    """Get the singleton transcription queue service."""
    return TranscriptionQueueService.get_instance(maxsize)
