"""
Redis Stream Service for Distributed Task Queue

Provides reliable distributed task queue using Redis Streams with Consumer Groups.
Features:
- Persistent task storage (survives crashes)
- Consumer groups for distributed workers
- Automatic task recovery (XAUTOCLAIM for orphaned tasks)
- Worker heartbeat mechanism
- Generic task type support via Protocol

Adapted from stt_service/service/redis/redis_stream_service.py /
orchestrator_service/services/redis/redis_stream_service.py (audio-ingestion
PLAN.md D28 point 3 -- reuse the existing Redis Stream consumer mechanism
as-is, only import paths changed). Logic deliberately NOT changed, including
2 known bugs documented in PLAN.md D28 point 3 (release_my_pending_tasks's
XCLAIM resetting idle-time instead of clearing it immediately, and
consumer-side "already processing" skip paths not ack/reject-ing the
message) -- both self-healing within ~60-90s via the orphan-recovery cycle,
not data loss, and left alone on purpose to avoid touching code shared with
stt_service/orchestrator_service. Fix in all 3 copies together if ever
addressed (same "sync by hand" note as PLAN.md D15 for the proto files).
"""

import asyncio
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import (
    Any,
    ClassVar,
    Dict,
    Generic,
    List,
    Optional,
    Type,
    TypeVar,
)

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from audio_processing_service.config import get_config
from audio_processing_service.infra.redis.connection_pool import get_connection_manager
from audio_processing_service.models.stream_base import (
    StreamTaskProtocol,
    StreamTaskStatus,
)
from audio_processing_service.utils.decode import decode_value

logger = logging.getLogger(__name__)


# Type variable bound to StreamTaskProtocol
T = TypeVar('T', bound=StreamTaskProtocol)


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
    current_task_id: Optional[str] = None
    tasks_processed: int = 0
    tasks_failed: int = 0


# ========================================
# Service Class
# ========================================

class RedisStreamService(Generic[T]):
    """
    Generic Redis Stream-based distributed task queue.

    Type parameter T: The task type this service handles (must implement StreamTaskProtocol)

    Keys used:
    - {stream_key}: Main task stream
    - {stream_key}:dlq: Dead letter queue
    - {stream_key}:tasks:{task_id}: Task metadata (Hash)
    - {stream_key}:workers: Worker heartbeats (Hash)
    - {stream_key}:stats: Queue statistics (Hash)
    """

    _instances: ClassVar[Dict[str, 'RedisStreamService']] = {}

    def __init__(
        self,
        task_class: Type[T],
        stream_key: Optional[str] = None,
        group_name: Optional[str] = None,
    ):
        self._task_class: Type[T] = task_class
        self._config = get_config().redis
        self._redis: Optional[Redis] = None
        self._consumer_id: str = self._generate_consumer_id()
        self._running = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._recovery_task: Optional[asyncio.Task] = None

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
        task_class: Type[T],
        stream_key: Optional[str] = None,
        group_name: Optional[str] = None,
    ) -> 'RedisStreamService[T]':
        effective_stream_key = stream_key
        instance_key = f"{task_class.__name__}:{effective_stream_key}"

        if instance_key not in cls._instances:
            cls._instances[instance_key] = cls(
                task_class=task_class,
                stream_key=stream_key,
                group_name=group_name,
            )

        return cls._instances[instance_key]

    def _generate_consumer_id(self) -> str:
        """Format: worker-{hostname}-{pid}-{uuid4_short}"""
        hostname = socket.gethostname()[:15]
        pid = os.getpid()
        unique_suffix = uuid.uuid4().hex[:8]
        return f"worker-{hostname}-{pid}-{unique_suffix}"

    async def connect(self) -> None:
        if self._redis is not None:
            logger.debug("Redis already connected")
            return

        try:
            manager = get_connection_manager()
            if not manager.is_connected:
                await manager.connect()
            self._redis = Redis(connection_pool=manager.get_pool())
            await self._redis.ping()
            logger.info(
                f"✅ Redis stream service using shared pool at "
                f"{self._config.host}:{self._config.port}"
            )

            await self._ensure_consumer_group()
            await self._init_stats()

        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            self._redis = None
            raise ConnectionError(f"Redis connection failed: {e}")

    async def _ensure_consumer_group(self) -> None:
        try:
            await self._redis.xgroup_create(
                self._stream_key,
                self._group_name,
                id="0",
                mkstream=True
            )
            logger.info(f"✅ Created consumer group '{self._group_name}' for stream '{self._stream_key}'")
        except ResponseError as e:
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group '{self._group_name}' already exists")
            else:
                raise

    async def _init_stats(self) -> None:
        stats_exist = await self._redis.exists(self._stats_key)
        if not stats_exist:
            await self._redis.hset(self._stats_key, mapping={
                "total_enqueued": "0",
                "total_processed": "0",
                "total_failed": "0",
                "total_retried": "0",
                "created_at": str(time.time()),
            })

    async def disconnect(self, release_pending: bool = True) -> None:
        if self._redis:
            if release_pending:
                released = await self.release_my_pending_tasks()
                if released > 0:
                    logger.info(f"Released {released} pending task(s) back to stream")

            await self._unregister_worker()
            await self._redis.close()
            self._redis = None

        logger.info("Redis stream client released (shared pool kept open)")

    async def release_my_pending_tasks(self) -> int:
        """Release all pending tasks owned by this consumer back to the stream."""
        if not self._redis:
            return 0

        try:
            result = await self._redis.xpending_range(
                self._stream_key,
                self._group_name,
                min="-",
                max="+",
                count=1000,
                consumername=self._consumer_id
            )

            if not result:
                return 0

            released_count = 0
            for entry in result:
                message_id = decode_value(entry["message_id"])

                try:
                    # NOTE (PLAN.md D28 point 3): XCLAIM with force=True actually
                    # RESETS this message's idle-time counter to 0, the opposite of
                    # releasing it "immediately" -- it now has to wait
                    # claim_min_idle_time_ms (60s default) again before
                    # claim_orphaned_tasks() on another consumer will pick it up.
                    # Self-healing (message stays in PEL, no data loss), just a
                    # ~60-90s delay. Left unfixed on purpose, see module docstring.
                    await self._redis.xclaim(
                        self._stream_key,
                        self._group_name,
                        "__released__",  # Dummy consumer that won't process
                        min_idle_time=0,
                        message_ids=[message_id],
                        force=True
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

    async def read_tasks(
        self,
        count: int = 1,
        block_ms: Optional[int] = None
    ) -> List[T]:
        """Read tasks from stream as consumer in group (XREADGROUP)."""
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        if block_ms is None:
            block_ms = self._config.block_timeout_ms

        try:
            result = await self._redis.xreadgroup(
                groupname=self._group_name,
                consumername=self._consumer_id,
                streams={self._stream_key: ">"},
                count=count,
                block=block_ms
            )

            if not result:
                return []

            tasks: List[T] = []
            for stream_name, messages in result:
                for message_id, data in messages:
                    message_id_str = decode_value(message_id)
                    task = self._task_class.from_stream_message(message_id_str, data)
                    tasks.append(task)

                    await self._redis.hset(
                        f"{self._tasks_prefix}:{task.task_id}",
                        mapping={
                            "status": StreamTaskStatus.PROCESSING.value,
                            "consumer_id": self._consumer_id,
                            "processing_started_at": str(time.time()),
                        }
                    )

                    logger.debug(f"📖 Read task {task.task_id} (msg_id={message_id_str})")

            return tasks

        except Exception as e:
            logger.error(f"Error reading from stream: {e}")
            raise

    async def acknowledge(self, task: T) -> bool:
        """Acknowledge successful task completion (XACK + XDEL)."""
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        try:
            result = await self._redis.xack(
                self._stream_key,
                self._group_name,
                task.message_id
            )

            if result > 0:
                deleted = await self._redis.xdel(
                    self._stream_key,
                    task.message_id
                )

                if deleted > 0:
                    logger.debug(f"🗑️ Deleted message {task.message_id} from stream")

                await self._redis.delete(f"{self._tasks_prefix}:{task.task_id}")
                await self._redis.hincrby(self._stats_key, "total_processed", 1)

                logger.info(f"✅ Acknowledged task {task.task_id}")
                return True
            else:
                logger.warning(f"Task {task.task_id} was not in pending list")
                return False

        except Exception as e:
            logger.error(f"Error acknowledging task {task.task_id}: {e}")
            raise

    async def reject(
        self,
        task: T,
        error: str,
        retry: bool = True
    ) -> bool:
        """Reject a failed task -- retry (re-add with incremented retry_count) or DLQ."""
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        new_retry_count = task.retry_count + 1
        should_retry = retry and new_retry_count <= self._config.max_retries

        try:
            if should_retry:
                task_data = task.to_dict()
                task_data["retry_count"] = str(new_retry_count)
                task_data["last_error"] = error[:500]

                new_message_id = await self._redis.xadd(
                    self._stream_key,
                    task_data,
                    maxlen=100000,
                    approximate=True
                )

                new_message_id_str = decode_value(new_message_id)

                await self._redis.hset(
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.PENDING.value,
                        "message_id": new_message_id_str,
                        "retry_count": str(new_retry_count),
                        "last_error": error[:500],
                        "retried_at": str(time.time()),
                    }
                )

                await self._redis.hincrby(self._stats_key, "total_retried", 1)

                logger.warning(
                    f"🔄 Task {task.task_id} failed (attempt {task.retry_count + 1}), "
                    f"re-queued as {new_message_id_str}: {error}"
                )
            else:
                task_data = task.to_dict()
                task_data["final_error"] = error[:500]
                task_data["dead_letter_at"] = str(time.time())

                await self._redis.xadd(self._dlq_key, task_data)

                await self._redis.hset(
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.DEAD_LETTER.value,
                        "final_error": error[:500],
                        "dead_letter_at": str(time.time()),
                    }
                )

                await self._redis.hincrby(self._stats_key, "total_failed", 1)

                logger.error(
                    f"Task {task.task_id} moved to dead letter queue after "
                    f"{task.retry_count + 1} attempts: {error}"
                )

            await self._redis.xack(self._stream_key, self._group_name, task.message_id)

            return should_retry

        except Exception as e:
            logger.error(f"Error rejecting task {task.task_id}: {e}")
            raise

    # ========================================
    # Pending Tasks & Recovery (XPENDING, XCLAIM)
    # ========================================

    async def get_pending_summary(self) -> Dict[str, Any]:
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        try:
            result = await self._redis.xpending(self._stream_key, self._group_name)

            if not result or (isinstance(result, (list, tuple)) and len(result) == 0):
                return {"pending_count": 0, "consumers": {}}

            if isinstance(result, dict):
                pending_count = result.get("pending", 0)
                min_id = result.get("min", None)
                max_id = result.get("max", None)
                consumers = result.get("consumers", [])
            elif isinstance(result, (list, tuple)) and len(result) >= 4:
                pending_count, min_id, max_id, consumers = result[:4]
            else:
                logger.warning(f"Unexpected xpending result format: {type(result)} - {result}")
                return {"pending_count": 0, "consumers": {}}

            consumer_info = {}
            if consumers:
                for consumer_data in consumers:
                    if isinstance(consumer_data, (list, tuple)) and len(consumer_data) >= 2:
                        name = decode_value(consumer_data[0])
                        count = int(consumer_data[1]) if consumer_data[1] else 0
                        consumer_info[name] = count

            return {
                "pending_count": pending_count if isinstance(pending_count, int) else 0,
                "min_message_id": decode_value(min_id),
                "max_message_id": decode_value(max_id),
                "consumers": consumer_info,
            }

        except Exception as e:
            logger.error(f"Error getting pending summary: {e}")
            raise

    async def get_pending_tasks(
        self,
        count: int = 100,
        min_idle_time_ms: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        try:
            result = await self._redis.xpending_range(
                self._stream_key,
                self._group_name,
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

                if min_idle_time_ms and idle_time_ms < min_idle_time_ms:
                    continue

                pending_tasks.append({
                    "message_id": message_id,
                    "consumer": consumer,
                    "idle_time_ms": idle_time_ms,
                    "delivery_count": delivery_count,
                })

            return pending_tasks

        except Exception as e:
            logger.error(f"Error getting pending tasks: {e}")
            raise

    async def claim_orphaned_tasks(
        self,
        min_idle_time_ms: Optional[int] = None,
        count: int = 10
    ) -> List[T]:
        """Claim orphaned tasks from crashed/stale workers (XAUTOCLAIM)."""
        if not self._redis:
            raise ConnectionError("Not connected to Redis")

        if min_idle_time_ms is None:
            min_idle_time_ms = self._config.claim_min_idle_time_ms

        try:
            result = await self._redis.xautoclaim(
                self._stream_key,
                self._group_name,
                self._consumer_id,
                min_idle_time=min_idle_time_ms,
                start_id="0-0",
                count=count
            )

            if not result or len(result) < 2:
                return []

            _, messages, deleted_ids = result if len(result) == 3 else (*result, [])

            if deleted_ids:
                logger.warning(f"Found {len(deleted_ids)} deleted messages during autoclaim")

            tasks: List[T] = []
            for message_id, data in messages:
                if data is None:
                    continue

                message_id_str = decode_value(message_id)
                task = self._task_class.from_stream_message(message_id_str, data)
                tasks.append(task)

                await self._redis.hset(
                    f"{self._tasks_prefix}:{task.task_id}",
                    mapping={
                        "status": StreamTaskStatus.PROCESSING.value,
                        "consumer_id": self._consumer_id,
                        "claimed_at": str(time.time()),
                    }
                )

                logger.info(
                    f"🔄 Claimed orphaned task {task.task_id} "
                    f"(was pending for {min_idle_time_ms}ms+)"
                )

            return tasks

        except Exception as e:
            logger.error(f"Error claiming orphaned tasks: {e}")
            raise

    # ========================================
    # Worker Heartbeat & Registration
    # ========================================

    async def register_worker(self) -> None:
        if not self._redis:
            return

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

        await self._redis.hset(self._workers_key, self._consumer_id, json.dumps(worker_info))
        logger.info(f"✅ Registered worker: {self._consumer_id}")

    async def update_heartbeat(self, current_task_id: Optional[str] = None) -> None:
        if not self._redis:
            return

        try:
            worker_data = await self._redis.hget(self._workers_key, self._consumer_id)
            if worker_data:
                info = json.loads(worker_data)
            else:
                info = {"consumer_id": self._consumer_id}

            info["last_heartbeat"] = str(time.time())
            if current_task_id is not None:
                info["current_task"] = current_task_id

            await self._redis.hset(self._workers_key, self._consumer_id, json.dumps(info))

        except Exception as e:
            logger.debug(f"Error updating heartbeat: {e}")

    async def increment_worker_stats(self, processed: int = 0, failed: int = 0) -> None:
        if not self._redis:
            return

        try:
            worker_data = await self._redis.hget(self._workers_key, self._consumer_id)
            if worker_data:
                info = json.loads(worker_data)
                info["tasks_processed"] = str(int(info.get("tasks_processed", 0)) + processed)
                info["tasks_failed"] = str(int(info.get("tasks_failed", 0)) + failed)
                await self._redis.hset(self._workers_key, self._consumer_id, json.dumps(info))
        except Exception as e:
            logger.debug(f"Error updating worker stats: {e}")

    async def _unregister_worker(self) -> None:
        if not self._redis:
            return

        try:
            await self._redis.hdel(self._workers_key, self._consumer_id)
            logger.info(f"Unregistered worker: {self._consumer_id}")
        except Exception as e:
            logger.debug(f"Error unregistering worker: {e}")

    async def get_active_workers(self) -> List[WorkerInfo]:
        if not self._redis:
            return []

        try:
            workers_data = await self._redis.hgetall(self._workers_key)
            workers = []

            now = time.time()
            timeout = self._config.worker_timeout_sec

            for consumer_id, data in workers_data.items():
                consumer_id_str = decode_value(consumer_id)
                try:
                    info = json.loads(data)
                    last_heartbeat = float(info.get("last_heartbeat", 0))

                    if now - last_heartbeat < timeout:
                        workers.append(WorkerInfo(
                            consumer_id=consumer_id_str,
                            hostname=info.get("hostname", "unknown"),
                            pid=int(info.get("pid", 0)),
                            last_heartbeat=last_heartbeat,
                            current_task_id=info.get("current_task") or None,
                            tasks_processed=int(info.get("tasks_processed", 0)),
                            tasks_failed=int(info.get("tasks_failed", 0)),
                        ))
                except (json.JSONDecodeError, ValueError):
                    continue

            return workers

        except Exception as e:
            logger.error(f"Error getting active workers: {e}")
            return []

    async def cleanup_stale_workers(self) -> int:
        if not self._redis:
            return 0

        try:
            workers_data = await self._redis.hgetall(self._workers_key)
            now = time.time()
            timeout = self._config.worker_timeout_sec * 3
            removed = 0

            for consumer_id, data in workers_data.items():
                consumer_id_str = decode_value(consumer_id)
                try:
                    info = json.loads(data)
                    last_heartbeat = float(info.get("last_heartbeat", 0))

                    if now - last_heartbeat > timeout:
                        await self._redis.hdel(self._workers_key, consumer_id_str)
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
        if self._running:
            return

        self._running = True
        await self.register_worker()

        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        self._recovery_task = asyncio.create_task(self._recovery_loop())

        logger.info("✅ Background tasks started (heartbeat + recovery)")

    async def stop_background_tasks(self) -> None:
        self._running = False

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

        if self._recovery_task:
            self._recovery_task.cancel()
            try:
                await self._recovery_task
            except asyncio.CancelledError:
                pass

        logger.info("Background tasks stopped")

    async def _heartbeat_loop(self) -> None:
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
        interval = self._config.worker_timeout_sec

        while self._running:
            try:
                await asyncio.sleep(interval)
                await self.cleanup_stale_workers()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Recovery loop error: {e}")
                await asyncio.sleep(interval)


# ========================================
# Factory Functions
# ========================================

def create_stream_service(
    task_class: Type[T],
    stream_key: str,
    group_name: str,
) -> 'RedisStreamService[T]':
    return RedisStreamService.get_instance(
        task_class=task_class,
        stream_key=stream_key,
        group_name=group_name,
    )
