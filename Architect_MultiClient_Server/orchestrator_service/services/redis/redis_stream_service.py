"""
Redis Stream Service for Distributed Task Queue

Provides reliable distributed task queue using Redis Streams with Consumer Groups.
Features:
- Persistent task storage (survives crashes)
- Consumer groups for distributed workers
- Automatic task recovery (XAUTOCLAIM for orphaned tasks)
- Worker heartbeat mechanism
- Generic task type support via Protocol

Redis Streams Commands Used:
- XADD: Add new task to stream
- XREADGROUP: Read tasks as consumer in group (blocking)
- XACK: Acknowledge task completion
- XPENDING: Check pending (in-progress) tasks
- XAUTOCLAIM: Claim orphaned tasks from crashed workers
"""

import asyncio
import contextlib
import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from typing import ClassVar, Generic, TypeVar, cast

from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.stream_base import (
    StreamTaskProtocol,
    StreamTaskStatus,
)
from orchestrator_service.services.redis.connection_pool import get_connection_manager
from orchestrator_service.utils.decode import decode_value
from orchestrator_service.utils.logger import get_logger
from redis.asyncio import Redis
from redis.exceptions import ResponseError

logger = get_logger(__name__)


# Type variable bound to StreamTaskProtocol
# Any class implementing the protocol can be used as T
T = TypeVar("T", bound=StreamTaskProtocol)


# ========================================
# Data Classes
# ========================================


@dataclass
class WorkerInfo:
    """Information about a worker in the consumer group."""

    consumer_id: str
    hostname: str
    pid: int
    last_heartbeat: float
    current_task_id: str | None = None
    tasks_processed: int = 0
    tasks_failed: int = 0

@dataclass
class PendingSummary:
    """Model for summary of pending tasks"""
    pending_count: int = 0
    min_message_id: str | None = None
    max_message_id: str | None = None
    consumers: dict[str, int] = field(default_factory=dict)

@dataclass
class PendingTask:
    """Model for detail of pending tasks"""
    message_id: str | None
    consumer: str | None
    idle_time_ms: int
    delivery_count: int


# ========================================
# Service Class
# ========================================


class RedisStreamService(Generic[T]):
    """
    Generic Redis Stream-based distributed task queue.

    Type parameter T: The task type this service handles (must implement StreamTaskProtocol)

    Architecture:
    - Uses Redis Streams for persistent, ordered task storage
    - Consumer Groups for distributing tasks across workers
    - Each worker has unique consumer ID: worker-{hostname}-{pid}-{uuid}
    - Orphaned tasks auto-claimed after idle timeout
    - Dead letter queue for tasks exceeding retry limit

    Keys used:
    - {stream_key}: Main task stream
    - {stream_key}:dlq: Dead letter queue
    - {stream_key}:tasks:{task_id}: Task metadata (Hash)
    - {stream_key}:workers: Worker heartbeats (Hash)
    - {stream_key}:stats: Queue statistics (Hash)
    """

    # Registry for singleton instances per (task_class, stream_key)
    _instances: ClassVar[dict[str, "RedisStreamService[StreamTaskProtocol]"]] = {}

    def __init__(
        self,
        task_class: type[T],
        stream_key: str | None = None,
        group_name: str | None = None,
    ):
        """
        Initialize Redis Stream service.

        Args:
            task_class: Class used to parse tasks (must implement StreamTaskProtocol)
            stream_key: Redis stream key (default: from config)
            group_name: Consumer group name (default: from config)
        """
        self._task_class: type[T] = task_class
        self._config = get_config().redis
        self._redis: Redis = get_connection_manager().get_client()
        self._consumer_id: str = self._generate_consumer_id()
        self._running = False
        self._is_setup = False
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._recovery_task: asyncio.Task[None] | None = None

        # Keys - use provided values or fall back to config
        self._stream_key = stream_key
        self._group_name = group_name
        self._dlq_key = f"{self._stream_key}:dlq"
        self._tasks_prefix = f"{self._stream_key}:tasks"
        self._workers_key = f"{self._stream_key}:workers"
        self._stats_key = f"{self._stream_key}:stats"

        logger.info(
            f"RedisStreamService[{task_class.__name__}] created - "
            f"stream='{self._stream_key}', consumer_id='{self._consumer_id}'"
        )

    @classmethod
    def get_instance(
        cls,
        task_class: type[T],
        stream_key: str | None = None,
        group_name: str | None = None,
    ) -> "RedisStreamService[T]":
        """
        Get or create singleton instance for the given task class and stream.

        Args:
            task_class: Class used to parse tasks
            stream_key: Redis stream key (default: from config)
            group_name: Consumer group name (default: from config)

        Returns:
            RedisStreamService instance for the specified configuration
        """
        effective_stream_key = stream_key
        instance_key = f"{task_class.__name__}:{effective_stream_key}"

        if instance_key not in cls._instances:
            new_instance = cls(
                task_class=task_class,
                stream_key=stream_key,
                group_name=group_name,
            )
            cls._instances[instance_key] = cast(
                "RedisStreamService[StreamTaskProtocol]",
                new_instance
            )

        return cast("RedisStreamService[T]", cls._instances[instance_key])

    def _generate_consumer_id(self) -> str:
        """Generate unique consumer ID for this worker.

        Format: worker-{hostname}-{pid}-{uuid4_short}

        The UUID suffix ensures uniqueness even when:
        - Running in containers with same hostname
        - PIDs are recycled after restart
        - Multiple workers start simultaneously
        """
        hostname = socket.gethostname()[:15]  # Truncate long hostnames
        pid = os.getpid()
        unique_suffix = uuid.uuid4().hex[:8]  # 8 char hex string
        return f"worker-{hostname}-{pid}-{unique_suffix}"

    async def connect(self) -> None:
        """
        Initialize Redis connection pool and create consumer group.

        Raises:
            ConnectionError: If cannot connect to Redis
        """
        if self._is_setup:
            logger.debug("Redis Stream Service already setup")
            return

        try:
            manager = get_connection_manager()
            if not manager.is_connected:
                await manager.connect()

            await self._redis.ping()   # type: ignore[misc]
            logger.info(f"✅ Redis stream service using shared pool at {self._config.host}:{self._config.port}")

            # Create consumer group (idempotent)
            await self._ensure_consumer_group()

            # Initialize stats
            await self._init_stats()

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise ConnectionError(f"Redis connection failed: {e}") from e

    async def _ensure_consumer_group(self) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            # XGROUP CREATE creates group, MKSTREAM creates stream if not exists
            await self._redis.xgroup_create(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                id="0",  # Start from beginning
                mkstream=True,
            )
            logger.info(f"✅ Created consumer group '{self._group_name}' for stream '{self._stream_key}'")
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                # Group already exists, this is fine
                logger.debug(f"Consumer group '{self._group_name}' already exists")
            else:
                raise

    async def _init_stats(self) -> None:
        """Initialize statistics hash."""
        stats_exist = await self._redis.exists(self._stats_key)  # type: ignore[arg-type, misc]
        if not stats_exist:
            await self._redis.hset(  # type: ignore[misc]
                self._stats_key,  # type: ignore[arg-type]
                mapping={
                    "total_enqueued": "0",
                    "total_processed": "0",
                    "total_failed": "0",
                    "total_retried": "0",
                    "created_at": str(time.time()),
                },
            )

    async def disconnect(self, release_pending: bool = True) -> None:
        """
        Close Redis connection with graceful shutdown.

        Args:
            release_pending: If True, release this worker's pending tasks back to stream
                           so other workers can pick them up
        """
        if release_pending:
            released = await self.release_my_pending_tasks()
            if released > 0:
                logger.info(f"Released {released} pending task(s) back to stream")

        # Remove worker registration
        await self._unregister_worker()
        await self._redis.close()
        self._is_setup = False

        logger.info("Redis stream client released (shared pool kept open)")

    async def release_my_pending_tasks(self) -> int:
        """
        Release all pending tasks owned by this consumer back to the stream.

        This allows other workers to claim them. Use during graceful shutdown
        or when a worker needs to stop temporarily.

        Returns:
            Number of tasks released
        """
        try:
            # Get pending tasks for this consumer
            result = await self._redis.xpending_range(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                min="-",
                max="+",
                count=1000,
                consumername=self._consumer_id,  # type: ignore[list-item]
            )

            if not result:
                return 0

            released_count = 0
            for entry in result:
                message_id = decode_value(entry["message_id"])

                if message_id is None:
                    continue

                # Re-add the task to stream (effectively release it)
                # We do this by setting the idle time in PEL very high so it can be auto-claimed
                # Or we can delete from PEL without ACK using XCLAIM to a dummy consumer
                # Best approach: Just don't ACK - the task will be auto-claimed by another worker

                # For immediate release, we can use XCLAIM to transfer to a special "released" consumer
                # Then those tasks will be auto-claimed since "released" won't process them

                # Actually, best approach is to XCLAIM with FORCE to reset idle time
                # This effectively makes the task available for auto-claim immediately
                try:
                    # XCLAIM to a non-existent consumer with FORCE resets the task
                    # Then it can be claimed by any active worker
                    await self._redis.xclaim(
                        self._stream_key,  # type: ignore[arg-type]
                        self._group_name,  # type: ignore[arg-type]
                        "__released__",  # Dummy consumer that won't process
                        min_idle_time=0,
                        message_ids=[message_id],  # type: ignore[arg-type]
                        force=True,
                    )
                    released_count += 1
                except Exception as e:
                    logger.warning(f"Failed to release task {message_id}: {e}")

            return released_count

        except Exception as e:
            logger.error(f"Error releasing pending tasks: {e}")
            return 0

    # ========================================
    # Task Operations (XADD, XACK, etc.)
    # ========================================

    async def read_tasks(self, count: int = 1, block_ms: int | None = None) -> list[T]:
        """
        Read tasks from stream as consumer in group.

        Uses XREADGROUP which:
        - Only returns messages not yet delivered to other consumers
        - Marks messages as pending (in-progress) for this consumer
        - Blocks if no messages available (if block_ms specified)

        Args:
            count: Maximum number of tasks to read
            block_ms: Milliseconds to block waiting for messages (None = don't block)

        Returns:
            List of tasks (type T)
        """
        if block_ms is None:
            block_ms = self._config.block_timeout_ms

        try:
            # XREADGROUP GROUP group consumer [COUNT count] [BLOCK ms] STREAMS key >
            # ">" means only new messages (not pending ones)
            result = await self._redis.xreadgroup(
                groupname=self._group_name,  # type: ignore[arg-type]
                consumername=self._consumer_id,  # type: ignore[arg-type]
                streams={self._stream_key: ">"},  # type: ignore[dict-item]
                count=count,
                block=block_ms,
            )

            if not result:
                return []

            tasks: list[T] = []
            for _, messages in result:
                for message_id, data in messages:
                    message_id_str = decode_value(message_id)
                    # Use injected task_class to parse the message
                    task = cast(T, self._task_class.from_stream_message(message_id_str, data))  # type: ignore[arg-type]
                    tasks.append(task)

                    # Update task status in metadata
                    await self._redis.hset(  # type: ignore[misc]
                        f"{self._tasks_prefix}:{task.task_id}",
                        mapping={
                            "status": StreamTaskStatus.PROCESSING.value,
                            "consumer_id": self._consumer_id,
                            "processing_started_at": str(time.time()),
                        },
                    )

                    logger.debug(f"📖 Read task {task.task_id} (msg_id={message_id_str})")

            return tasks

        except Exception as e:
            logger.error(f"Error reading from stream: {e}")
            raise

    async def acknowledge(self, task: T) -> bool:
        """
        Acknowledge successful task completion.

        Removes the message from pending entries list (PEL) and deletes it from stream.

        Args:
            task: The completed task (type T)

        Returns:
            True if acknowledged successfully
        """
        try:
            # XACK removes message from pending entries
            result = await self._redis.xack(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                task.message_id,
            )

            if result > 0:
                # XDEL removes the message completely from stream
                deleted = await self._redis.xdel(self._stream_key, task.message_id)  # type: ignore[arg-type]

                if deleted > 0:
                    logger.debug(f"🗑️ Deleted message {task.message_id} from stream")

                # Delete task metadata since task is completed and no longer needed
                await self._redis.delete(f"{self._tasks_prefix}:{task.task_id}")  # type: ignore[misc]

                # Update stats
                await self._redis.hincrby(self._stats_key, "total_processed", 1)  # type: ignore[misc]

                logger.info(f"✅ Acknowledged task {task.task_id}")
                return True
            else:
                logger.warning(f"Task {task.task_id} was not in pending list")
                return False

        except Exception as e:
            logger.error(f"Error acknowledging task {task.task_id}: {e}")
            raise

    async def reject(self, task: T, error: str, retry: bool = True) -> bool:
        """
        Reject a failed task.

        If retry=True and retry_count < max_retries:
            - Re-add to stream with incremented retry_count
        Else:
            - Move to dead letter queue

        In both cases, ACK the original message to remove from pending.

        Args:
            task: The failed task (type T)
            error: Error message
            retry: Whether to retry the task

        Returns:
            True if task was re-queued, False if sent to DLQ
        """
        new_retry_count = task.retry_count + 1
        should_retry = retry and new_retry_count <= self._config.max_retries

        try:
            if should_retry:
                # Re-add to stream with incremented retry count
                task_data = task.to_dict()
                task_data["retry_count"] = str(new_retry_count)
                task_data["last_error"] = error[:500]  # Truncate long errors

                new_message_id = await self._redis.xadd(
                    self._stream_key,  # type: ignore[arg-type]
                    task_data,  # type: ignore[arg-type]
                    maxlen=100000,
                    approximate=True,
                )

                new_message_id_str = decode_value(new_message_id)

                # Update task metadata hash to track retry
                await self._redis.hset(  # type: ignore[misc]
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.PENDING.value,
                        "message_id": new_message_id_str,
                        "retry_count": str(new_retry_count),
                        "last_error": error[:500],
                        "retried_at": str(time.time()),
                    },
                )

                # Update stats
                await self._redis.hincrby(self._stats_key, "total_retried", 1)  # type: ignore[misc]

                logger.warning(
                    f"🔄 Task {task.task_id} failed (attempt {task.retry_count + 1}), "
                    f"re-queued as {new_message_id_str}: {error}"
                )
            else:
                # Move to dead letter queue
                task_data = task.to_dict()
                task_data["final_error"] = error[:500]
                task_data["dead_letter_at"] = str(time.time())

                await self._redis.xadd(
                    self._dlq_key,  # type: ignore[arg-type]
                    task_data,  # type: ignore[arg-type]
                )

                # Update task status
                await self._redis.hset(  # type: ignore[misc]
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.DEAD_LETTER.value,
                        "final_error": error[:500],
                        "dead_letter_at": str(time.time()),
                    },
                )

                # Update stats
                await self._redis.hincrby(self._stats_key, "total_failed", 1)  # type: ignore[misc]

                logger.error(
                    f"Task {task.task_id} moved to dead letter queue after {task.retry_count + 1} attempts: {error}"
                )

            # Always ACK the original message
            await self._redis.xack(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                task.message_id,
            )

            return should_retry

        except Exception as e:
            logger.error(f"Error rejecting task {task.task_id}: {e}")
            raise

    # ========================================
    # Pending Tasks & Recovery (XPENDING, XCLAIM)
    # ========================================

    async def get_pending_summary(self) -> PendingSummary:
        """
        Get summary of pending (in-progress) tasks.

        Returns:
            Dict with pending count, min/max message IDs, and consumer breakdown
        """
        try:
            # XPENDING stream group - returns summary
            result = await self._redis.xpending(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
            )

            # Handle empty or None result
            if not result or (isinstance(result, list | tuple) and len(result) == 0):
                return PendingSummary()

            # Handle dict format (some redis-py versions return dict)
            if isinstance(result, dict):
                pending_count = result.get("pending", 0)
                min_id = result.get("min", None)
                max_id = result.get("max", None)
                consumers = result.get("consumers", [])
            # Handle tuple/list format [pending_count, min_id, max_id, consumers]
            elif isinstance(result, list | tuple) and len(result) >= 4:
                pending_count, min_id, max_id, consumers = result[:4]
            else:
                logger.warning(f"Unexpected xpending result format: {type(result)} - {result}")
                return PendingSummary()

            # Decode consumer info
            consumer_info: dict[str, int] = {}
            if consumers:
                for consumer_data in consumers:
                    if isinstance(consumer_data, list | tuple) and len(consumer_data) >= 2:
                        name = decode_value(consumer_data[0])
                        if name is not None:
                            count = int(consumer_data[1]) if consumer_data[1] else 0
                            consumer_info[name] = count

            return PendingSummary(
                pending_count=pending_count if isinstance(pending_count, int) else 0,
                min_message_id=decode_value(min_id),
                max_message_id=decode_value(max_id),
                consumers=consumer_info,
            )

        except Exception as e:
            logger.error(f"Error getting pending summary: {e}")
            raise

    async def get_pending_tasks(self, count: int = 100, min_idle_time_ms: int | None = None) -> list[PendingTask]:
        """
        Get detailed info about pending tasks.

        Args:
            count: Maximum number of tasks to return
            min_idle_time_ms: Only return tasks idle for at least this long

        Returns:
            List of pending task info dicts
        """
        try:
            # XPENDING stream group - start end count [consumer]
            result = await self._redis.xpending_range(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                min="-",
                max="+",
                count=count,
            )

            pending_tasks = []
            for entry in result:
                message_id = decode_value(entry["message_id"])
                consumer = decode_value(entry["consumer"])

                idle_time_ms = entry["time_since_delivered"]
                delivery_count = entry["times_delivered"]

                # Filter by idle time if specified
                if min_idle_time_ms and idle_time_ms < min_idle_time_ms:
                    continue

                pending_tasks.append(
                    PendingTask(
                        message_id=message_id,
                        consumer=consumer,
                        idle_time_ms=idle_time_ms,
                        delivery_count=delivery_count,
                    )
                )

            return pending_tasks

        except Exception as e:
            logger.error(f"Error getting pending tasks: {e}")
            raise

    async def claim_orphaned_tasks(self, min_idle_time_ms: int | None = None, count: int = 10) -> list[T]:
        """
        Claim orphaned tasks from crashed/stale workers.

        Uses XAUTOCLAIM which atomically:
        1. Finds pending messages idle > min_idle_time_ms
        2. Claims them for this consumer
        3. Returns the claimed messages

        Args:
            min_idle_time_ms: Minimum idle time to consider task orphaned
            count: Maximum tasks to claim

        Returns:
            List of claimed tasks (type T)
        """
        if min_idle_time_ms is None:
            min_idle_time_ms = self._config.claim_min_idle_time_ms

        try:
            # XAUTOCLAIM stream group consumer min-idle-time start [COUNT count]
            result = await self._redis.xautoclaim(
                self._stream_key,  # type: ignore[arg-type]
                self._group_name,  # type: ignore[arg-type]
                self._consumer_id,  # type: ignore[arg-type]
                min_idle_time=min_idle_time_ms,
                start_id="0-0",
                count=count,
            )

            if not result or len(result) < 2:
                return []

            # result = (next_start_id, [(msg_id, data), ...], [deleted_ids])
            _, messages, deleted_ids = result if len(result) == 3 else (*result, [])

            if deleted_ids:
                logger.warning(f"Found {len(deleted_ids)} deleted messages during autoclaim")

            tasks: list[T] = []
            for message_id, data in messages:
                if data is None:
                    # Message was deleted
                    continue

                message_id_str = decode_value(message_id)
                # Use injected task_class to parse the message
                task = cast(T, self._task_class.from_stream_message(message_id_str, data))  # type: ignore[arg-type]
                tasks.append(task)

                # Update task metadata
                await self._redis.hset(  # type: ignore[misc]
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.PROCESSING.value,
                        "consumer_id": self._consumer_id,
                        "claimed_at": str(time.time()),
                    },
                )

                logger.info(f"🔄 Claimed orphaned task {task.task_id} (was pending for {min_idle_time_ms}ms+)")

            return tasks

        except Exception as e:
            logger.error(f"Error claiming orphaned tasks: {e}")
            raise

    # ========================================
    # Worker Heartbeat & Registration
    # ========================================

    async def register_worker(self) -> None:
        """Register this worker with heartbeat."""
        worker_info = {
            "consumer_id": self._consumer_id,
            "hostname": socket.gethostname(),
            "pid": str(os.getpid()),
            "registered_at": str(time.time()),
            "last_heartbeat": str(time.time()),
            "current_task": "",
            "tasks_processed": "0",
            "tasks_failed": "0",
        }

        await self._redis.hset(  # type: ignore[misc]
            self._workers_key,  # type: ignore[arg-type]
            self._consumer_id,  # type: ignore[arg-type]
            json.dumps(worker_info),
        )
        logger.info(f"✅ Registered worker: {self._consumer_id}")

    async def update_heartbeat(self, current_task_id: str | None = None) -> None:
        """Update worker heartbeat."""
        try:
            # Get current worker info
            worker_data = await self._redis.hget(  # type: ignore[misc]
                self._workers_key,  # type: ignore[arg-type]
                self._consumer_id,  # type: ignore[arg-type]
            )

            info = json.loads(worker_data) if worker_data else {"consumer_id": self._consumer_id}

            info["last_heartbeat"] = str(time.time())
            if current_task_id is not None:
                info["current_task"] = current_task_id

            await self._redis.hset(  # type: ignore[misc]
                self._workers_key,  # type: ignore[arg-type]
                self._consumer_id,  # type: ignore[arg-type]
                json.dumps(info),
            )

        except Exception as e:
            logger.debug(f"Error updating heartbeat: {e}")

    async def increment_worker_stats(self, processed: int = 0, failed: int = 0) -> None:
        """Increment worker statistics."""
        try:
            worker_data = await self._redis.hget(  # type: ignore[misc]
                self._workers_key,  # type: ignore[arg-type]
                self._consumer_id,  # type: ignore[arg-type]
            )
            if worker_data:
                info = json.loads(worker_data)
                info["tasks_processed"] = str(int(info.get("tasks_processed", 0)) + processed)
                info["tasks_failed"] = str(int(info.get("tasks_failed", 0)) + failed)
                await self._redis.hset(  # type: ignore[misc]
                    self._workers_key,  # type: ignore[arg-type]
                    self._consumer_id,  # type: ignore[arg-type]
                    json.dumps(info),
                )
        except Exception as e:
            logger.debug(f"Error updating worker stats: {e}")

    async def _unregister_worker(self) -> None:
        """Unregister this worker on shutdown."""
        try:
            await self._redis.hdel(  # type: ignore[misc]
                self._workers_key,  # type: ignore[arg-type]
                self._consumer_id,  # type: ignore[arg-type]
            )
            logger.info(f"Unregistered worker: {self._consumer_id}")
        except Exception as e:
            logger.debug(f"Error unregistering worker: {e}")

    async def get_active_workers(self) -> list[WorkerInfo]:
        """Get list of active workers."""
        try:
            workers_data = await self._redis.hgetall(self._workers_key)  # type: ignore[arg-type, misc]
            workers = []

            now = time.time()
            timeout = self._config.worker_timeout_sec

            for consumer_id, data in workers_data.items():
                consumer_id_str = decode_value(consumer_id)
                if not consumer_id_str:
                    continue

                try:
                    info = json.loads(data)
                    last_heartbeat = float(info.get("last_heartbeat", 0))

                    # Only include workers with recent heartbeat
                    if now - last_heartbeat < timeout:
                        workers.append(
                            WorkerInfo(
                                consumer_id=consumer_id_str,
                                hostname=info.get("hostname", "unknown"),
                                pid=int(info.get("pid", 0)),
                                last_heartbeat=last_heartbeat,
                                current_task_id=info.get("current_task") or None,
                                tasks_processed=int(info.get("tasks_processed", 0)),
                                tasks_failed=int(info.get("tasks_failed", 0)),
                            )
                        )
                except (json.JSONDecodeError, ValueError):
                    continue

            return workers

        except Exception as e:
            logger.error(f"Error getting active workers: {e}")
            return []

    async def cleanup_stale_workers(self) -> int:
        """Remove stale worker entries. Returns count removed."""
        try:
            workers_data = await self._redis.hgetall(self._workers_key)  # type: ignore[arg-type, misc]
            now = time.time()
            timeout = self._config.worker_timeout_sec * 3  # 3x timeout before cleanup
            removed = 0

            for consumer_id, data in workers_data.items():
                consumer_id_str = decode_value(consumer_id)
                try:
                    info = json.loads(data)
                    last_heartbeat = float(info.get("last_heartbeat", 0))

                    if now - last_heartbeat > timeout:
                        await self._redis.hdel(self._workers_key, consumer_id_str)  # type: ignore[arg-type, misc]
                        removed += 1
                        logger.info(f"🗑️  Removed stale worker: {consumer_id_str}")
                except (json.JSONDecodeError, ValueError):
                    continue

            return removed

        except Exception as e:
            logger.error(f"Error cleaning up stale workers: {e}")
            return 0

    # ========================================
    # Background Tasks (Heartbeat, Recovery)
    # ========================================

    async def start_background_tasks(self) -> None:
        """Start heartbeat and recovery background tasks."""
        if self._running:
            return

        self._running = True
        await self.register_worker()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._recovery_task = asyncio.create_task(self._recovery_loop())

        logger.info("✅ Background tasks started (heartbeat + recovery)")

    async def stop_background_tasks(self) -> None:
        """Stop all background tasks."""
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task

        if self._recovery_task:
            self._recovery_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._recovery_task

        logger.info("Background tasks stopped")

    async def _heartbeat_loop(self) -> None:
        """Periodic heartbeat update."""
        interval = self._config.heartbeat_interval_sec

        while self._running:
            try:
                await self.update_heartbeat()
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug(f"Heartbeat error: {e}")
                await asyncio.sleep(interval)

    async def _recovery_loop(self) -> None:
        """Periodically check for and claim orphaned tasks."""
        # Run less frequently than heartbeat
        interval = self._config.worker_timeout_sec

        while self._running:
            try:
                await asyncio.sleep(interval)

                # Cleanup stale workers
                await self.cleanup_stale_workers()

                # Note: We don't auto-process claimed tasks here
                # The consumer loop should call claim_orphaned_tasks when idle

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recovery loop error: {e}")
                await asyncio.sleep(interval)


# ========================================
# Factory Functions
# ========================================


def create_stream_service(
    task_class: type[T],
    stream_key: str,
    group_name: str,
) -> "RedisStreamService[T]":
    """
    Create or get a RedisStreamService for a specific task type.

    Uses singleton pattern - returns existing instance if one exists
    for the given (task_class, stream_key) combination.

    Args:
        task_class: Class implementing StreamTaskProtocol
        stream_key: Redis stream key (default: from config)
        group_name: Consumer group name (default: from config)

    Returns:
        RedisStreamService instance for the specified task type
    """
    return RedisStreamService.get_instance(
        task_class=task_class, stream_key=stream_key, group_name=group_name,
    )
