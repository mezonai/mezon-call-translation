"""
Redis-based Save Transcription Queue Service

Consumer service that receives SaveTranscriptionTask batches from Redis
and saves them progressively to MongoDB.
"""

import asyncio
import time
from typing import Optional

from orchestrator_service.models.save_transcription_task import SaveTranscriptionTask
from orchestrator_service.services.redis.redis_stream_service import (
    RedisStreamService,
    create_stream_service,
)
from orchestrator_service.services.mongodb_service import MongoDBService
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decorator import singleton

logger = get_logger(__name__)

# Redis stream configuration
SAVE_STREAM_KEY = "save_transcription:stream"
SAVE_GROUP_NAME = "save-transcription-workers"

# Task names
CONSUMER_TASK_NAME = "redis-save-transcription-consumer"
ORPHAN_RECOVERY_TASK_NAME = "redis-save-orphan-recovery"


def get_save_stream_service() -> 'RedisStreamService[SaveTranscriptionTask]':
    """
    Get singleton RedisStreamService for save transcription tasks.
    
    Returns:
        RedisStreamService[SaveTranscriptionTask] singleton instance
    """
    return create_stream_service(
        task_class=SaveTranscriptionTask,
        stream_key=SAVE_STREAM_KEY,
        group_name=SAVE_GROUP_NAME
    )

@singleton
class RedisSaveTranscriptionService:
    """
    Redis Stream-based save transcription consumer service.
    
    Workflow:
    1. Receive SaveTranscriptionTask batches from Redis
    2. Save segments to MongoDB progressively
    3. If is_final=True, update track status to "completed"
    4. ACK task on success, retry on failure
    
    Features:
    - Multiple workers can process batches in parallel
    - Automatic recovery of orphaned tasks
    - Dead letter queue for failed tasks
    """
    
    def __init__(self):
        self._redis_service: Optional[RedisStreamService[SaveTranscriptionTask]] = None
        self._mongodb_service: Optional[MongoDBService] = None
        self._consumer_task: Optional[asyncio.Task] = None
        self._orphan_recovery_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Stats
        self._local_stats = {
            "batches_processed": 0,
            "batches_failed": 0,
            "total_segments_saved": 0,
            "started_at": None,
        }
        
        logger.info("RedisSaveTranscriptionService initialized")
    
    
    async def connect(self) -> None:
        """Connect to Redis and MongoDB."""
        if self._redis_service is not None:
            return
        
        # Connect to Redis
        self._redis_service = get_save_stream_service()
        await self._redis_service.connect()
        logger.info("✅ RedisSaveTranscriptionService connected to Redis")
        
        # Connect to MongoDB
        self._mongodb_service = MongoDBService()
        await self._mongodb_service.connect()
        logger.info("✅ RedisSaveTranscriptionService connected to MongoDB")
    
    async def start(self) -> None:
        """
        Start the consumer loop and background tasks.
        
        This will:
        1. Connect to Redis and MongoDB
        2. Start background tasks (heartbeat, recovery)
        3. Start consumer loop to process save tasks
        """
        if self._running:
            logger.warning("RedisSaveTranscriptionService already running")
            return
        
        # Ensure connected
        await self.connect()
        
        self._running = True
        self._local_stats["started_at"] = time.time()
        
        # Start Redis background tasks
        await self._redis_service.start_background_tasks()
        
        # Start consumer loop
        self._consumer_task = asyncio.create_task(
            self._consumer_loop(),
            name=CONSUMER_TASK_NAME
        )
        
        # Start orphan recovery
        self._orphan_recovery_task = asyncio.create_task(
            self._orphan_recovery_loop(),
            name=ORPHAN_RECOVERY_TASK_NAME
        )
        
        logger.info(
            f"✅ RedisSaveTranscriptionService started\n"
            f"   Consumer ID: {self._redis_service._consumer_id}\n"
            f"   Stream: {self._redis_service._stream_key}\n"
            f"   Group: {self._redis_service._group_name}"
        )
    
    async def stop(self) -> None:
        """Stop the service gracefully."""
        logger.info("Stopping RedisSaveTranscriptionService...")
        self._running = False
        
        # Cancel consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        
        # Cancel orphan recovery
        if self._orphan_recovery_task:
            self._orphan_recovery_task.cancel()
            try:
                await self._orphan_recovery_task
            except asyncio.CancelledError:
                pass
        
        # Stop background tasks
        if self._redis_service:
            await self._redis_service.stop_background_tasks()
            await self._redis_service.disconnect()
        
        # Disconnect MongoDB
        if self._mongodb_service:
            await self._mongodb_service.disconnect()
        
        logger.info("✅ RedisSaveTranscriptionService stopped")
    
    async def _consumer_loop(self) -> None:
        """
        Main consumer loop - read and process save tasks from Redis.
        """
        logger.info("🔄 Save consumer loop started - waiting for tasks...")
        
        while self._running:
            try:
                # Update heartbeat
                await self._redis_service.update_heartbeat(current_task_id=None)
                
                # Read tasks from Redis (blocking)
                tasks = await self._redis_service.read_tasks(
                    count=1,
                    block_ms=self._redis_service._config.block_timeout_ms
                )
                
                if not tasks:
                    continue
                
                for task in tasks:
                    # Update heartbeat with current task
                    await self._redis_service.update_heartbeat(
                        current_task_id=task.task_id
                    )
                    
                    # Process the save task
                    success = await self._process_save_task(task)
                    
                    # Update worker stats
                    if success:
                        await self._redis_service.increment_worker_stats(processed=1)
                    else:
                        await self._redis_service.increment_worker_stats(failed=1)
                
            except asyncio.CancelledError:
                logger.info("Save consumer loop cancelled")
                break
            except ConnectionError as e:
                logger.error(f"Redis connection error: {e}")
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Save consumer loop error: {e}", exc_info=True)
                await asyncio.sleep(1)
        
        logger.info("Save consumer loop ended")
    
    async def _process_save_task(self, task: SaveTranscriptionTask) -> bool:
        """
        Process a save transcription task.
        
        Args:
            task: SaveTranscriptionTask with batch of segments
            
        Returns:
            True if successful, False otherwise
        """
        logger.info(
            f"💾 Processing save task {task.task_id}: "
            f"track={task.track_ref_id}, chunk={task.chunk_index}, "
            f"segments={task.item_count}, final={task.is_final}"
        )
        
        try:
            # Save segments to MongoDB
            await self._mongodb_service.append_transcript_chunk(
                track_ref_id=task.track_ref_id,
                new_segments=task.segments
            )
            
            logger.info(
                f"💾 Saved {task.item_count} segments for track {task.track_ref_id} "
                f"(chunk {task.chunk_index}, time {task.start_time:.1f}-{task.end_time:.1f}s)"
            )
            
            # If this is the final chunk, update track status
            if task.is_final:
                logger.info(
                    f"🏁 Final chunk received for track {task.track_ref_id}, "
                    f"updating status to 'completed'"
                )
                
                success = await self._mongodb_service.update_track_status(
                    track_ref_id=task.track_ref_id,
                    status="completed"
                )
                
                if success:
                    # Log final summary
                    logger.info(
                        f"\n{'='*60}\n"
                        f"✅ TRANSCRIPTION SAVED COMPLETE\n"
                        f"{'='*60}\n"
                        f"Track ID: {task.track_ref_id}\n"
                        f"Status: completed\n"
                        f"{'='*60}"
                    )
                else:
                    logger.warning(
                        f"Failed to update status for track {task.track_ref_id}"
                    )
            
            # ACK the task
            await self._redis_service.acknowledge(task)
            
            # Update local stats
            self._local_stats["batches_processed"] += 1
            self._local_stats["total_segments_saved"] += task.item_count
            
            return True
            
        except Exception as e:
            logger.error(
                f"❌ Failed to save batch for track {task.track_ref_id}: {e}",
                exc_info=True
            )
            
            # Reject with retry
            await self._redis_service.reject(task, error=str(e))
            
            self._local_stats["batches_failed"] += 1
            return False
    
    async def _orphan_recovery_loop(self) -> None:
        """
        Periodically recover orphaned tasks from crashed workers.
        """
        logger.info("🔄 Orphan recovery loop started")
        
        # Wait a bit before first check
        await asyncio.sleep(30)
        
        while self._running:
            try:
                await asyncio.sleep(60)  # Check every minute
                
                if not self._running:
                    break
                
                # Claim orphaned tasks
                claimed_tasks = await self._redis_service.claim_orphaned_tasks(count=5)
                
                if claimed_tasks:
                    logger.info(
                        f"🔄 Claimed {len(claimed_tasks)} orphaned task(s) "
                        f"from crashed workers"
                    )
                    
                    # Process claimed tasks
                    for task in claimed_tasks:
                        task.retry_count += 1
                        
                        await self._redis_service.update_heartbeat(
                            current_task_id=task.task_id
                        )
                        
                        success = await self._process_save_task(task)
                        
                        if success:
                            await self._redis_service.increment_worker_stats(processed=1)
                        else:
                            await self._redis_service.increment_worker_stats(failed=1)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Orphan recovery error: {e}", exc_info=True)
                await asyncio.sleep(10)
        
        logger.info("Orphan recovery loop ended")
    
    def get_stats(self) -> dict:
        """Get local statistics."""
        uptime = None
        if self._local_stats["started_at"]:
            uptime = time.time() - self._local_stats["started_at"]
        
        return {
            **self._local_stats,
            "uptime_seconds": uptime,
            "running": self._running,
        }
