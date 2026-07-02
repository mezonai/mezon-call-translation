"""
Generic Queue Service for orchestrator.

Provides queue monitoring and management functionality for any task type.
Supports multiple queue types through generic type parameter.
"""

import logging
from typing import Any, ClassVar, Generic, TypeVar

from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.save_transcription_task import SaveTranscriptionTask
from orchestrator_service.models.stream_base import ProducerTaskProtocol
from orchestrator_service.models.transcription_task import TranscriptionTask
from orchestrator_service.services.redis.redis_producer_service import (
    RedisProducerService,
    create_producer_service,
)

logger = logging.getLogger(__name__)

# Type variable for generic task types
T = TypeVar("T", bound=ProducerTaskProtocol)


class QueueService(Generic[T]):
    """
    Generic service for queue monitoring and management.

    Type parameter T: Task type (must implement ProducerTaskProtocol)

    Wraps the RedisProducerService to provide queue monitoring capabilities
    for any task type.
    """

    # Singleton registry per (task_class, stream_key)
    _instances: ClassVar[dict[str, "QueueService"]] = {}

    def __init__(
        self,
        task_class: type[T],
        stream_key: str,
        queue_name: str,
    ):
        """
        Initialize queue service.

        Args:
            task_class: Task class implementing ProducerTaskProtocol
            stream_key: Redis stream key (default: from config)
            queue_name: Logical queue name for identification
        """
        self._task_class = task_class
        self._stream_key = stream_key
        self._queue_name = queue_name or task_class.__name__.replace("Task", "").lower()
        self.config = get_config()
        self._producer: RedisProducerService[T] = create_producer_service(
            task_class=self._task_class, stream_key=self._stream_key
        )

        logger.info(
            f"QueueService[{self._task_class.__name__}] created - "
            f"queue='{self._queue_name}', stream='{stream_key or 'default'}'"
        )

    @classmethod
    def get_instance(
        cls,
        task_class: type[T],
        stream_key: str,
        queue_name: str,
    ) -> "QueueService[T]":
        """
        Get or create singleton instance for task class and stream.

        Args:
            task_class: Task class implementing ProducerTaskProtocol
            stream_key: Redis stream key (default: from config)
            queue_name: Logical queue name for identification

        Returns:
            QueueService singleton instance
        """
        instance_key = f"{task_class.__name__}:{stream_key}"

        if instance_key not in cls._instances:
            cls._instances[instance_key] = cls(
                task_class=task_class,
                stream_key=stream_key,
                queue_name=queue_name,
            )

        return cls._instances[instance_key]

    @property
    def queue_name(self) -> str:
        """Get the queue name."""
        return self._queue_name

    @property
    def stream_key(self) -> str:
        """Get the stream key."""
        producer = self._producer
        key_from_producer = producer.stream_key
        return key_from_producer or self._stream_key or self.config.redis.stream_key

    @property
    def task_class(self) -> type[T]:
        """Get the task class."""
        return self._task_class

    async def get_stats(self) -> dict[str, Any]:
        """
        Get comprehensive queue statistics.

        Returns:
            Dict with keys: queue_name, stream_key, stream_length,
                          total_enqueued, total_processed, total_failed,
                          pending_count, active_workers
        """
        try:
            stats = await self._producer.get_queue_stats()

            # Transform to expected format with queue identification
            return {
                "queue_name": self._queue_name,
                "stream_key": stats.get("stream_key", self.stream_key),
                "stream_length": stats.get("stream_length", 0),
                "total_enqueued": stats.get("total_enqueued", 0),
                "total_processed": stats.get("total_processed", 0),
                "total_failed": stats.get("total_failed", 0),
                "pending_count": stats.get("stream_length", 0),  # Stream length = pending tasks
                "active_workers": stats.get("active_workers", 0),  # From Redis workers hash
            }

        except Exception as e:
            logger.error(f"Failed to get queue stats for {self._queue_name}: {e}")
            return {
                "queue_name": self._queue_name,
                "stream_key": self.stream_key,
                "stream_length": 0,
                "total_enqueued": 0,
                "total_processed": 0,
                "total_failed": 0,
                "pending_count": 0,
                "active_workers": 0,
            }

    async def get_task(self, task_id: str) -> dict[str, Any] | None:
        """
        Get status of a specific task.

        Args:
            task_id: The task ID

        Returns:
            Dict with task metadata, or None if not found
        """
        try:
            # Get task metadata from Redis hash
            task_key = f"{self._producer._config.tasks_prefix}:{task_id}"
            task_data_raw = await self._producer._redis.hgetall(task_key)  # type: ignore[misc]

            if not task_data_raw:
                return None

            # Decode bytes to strings
            task_data = {
                k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                for k, v in task_data_raw.items()
            }

            return task_data

        except Exception as e:
            logger.error(f"Failed to get task {task_id}: {e}")
            return None

    async def get_pending_tasks(self) -> list[dict[str, Any]]:
        """
        Get list of pending tasks.

        Note: As a producer, we have limited visibility into pending tasks.
        This returns basic info from the stream.

        Returns:
            List of pending task dictionaries
        """
        try:
            # Read pending messages from stream (last 100)
            messages = await self._producer._redis.xrange(self.stream_key, count=100)

            pending_tasks = []
            for message_id, data in messages:
                # Decode message
                task_data = {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }

                pending_tasks.append(
                    {
                        "message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                        "task_id": task_data.get("task_id"),
                        "filename": task_data.get("filename"),
                        "created_at": float(task_data.get("created_at", 0)),
                        "status": "pending",
                    }
                )

            return pending_tasks

        except Exception as e:
            logger.error(f"Failed to get pending tasks: {e}")
            return []

    async def get_dlq_tasks(self, limit: int = 100) -> list[dict[str, Any]]:
        """
        Get list of tasks in Dead Letter Queue.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of DLQ task dictionaries with error info
        """
        try:
            dlq_stream_key = f"{self._producer.stream_key}:dlq"

            # Read messages from DLQ stream
            messages = await self._producer._redis.xrange(dlq_stream_key, count=limit)

            dlq_tasks = []
            for message_id, data in messages:
                # Decode message
                task_data = {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }

                dlq_tasks.append(
                    {
                        "message_id": message_id.decode() if isinstance(message_id, bytes) else message_id,
                        "task_id": task_data.get("task_id"),
                        "filename": task_data.get("filename"),
                        "created_at": float(task_data.get("created_at", 0)),
                        "dead_letter_at": float(task_data.get("dead_letter_at", 0)),
                        "final_error": task_data.get("final_error", "Unknown error"),
                        "retry_count": int(task_data.get("retry_count", 0)),
                        "status": "dead_letter",
                    }
                )

            return dlq_tasks

        except Exception as e:
            logger.error(f"Failed to get DLQ tasks: {e}")
            return []

    async def retry_dlq_task(self, task_id: str) -> bool:
        """
        Retry a single task from DLQ.

        Moves task from DLQ back to main queue with retry_count reset.

        Args:
            task_id: The task ID to retry

        Returns:
            True if retry was successful, False otherwise
        """
        try:
            dlq_stream_key = f"{self._producer.stream_key}:dlq"

            # Find the task in DLQ
            messages = await self._producer._redis.xrange(dlq_stream_key)

            for message_id, data in messages:
                task_data = {
                    k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }

                if task_data.get("task_id") == task_id:
                    # Reset retry_count and re-enqueue to main stream
                    task_data["retry_count"] = "0"

                    # Remove dead letter tracking fields
                    task_data.pop("final_error", None)
                    task_data.pop("dead_letter_at", None)

                    # Re-enqueue to main stream
                    await self._producer._redis.xadd(self.stream_key, task_data)

                    # Remove from DLQ
                    await self._producer._redis.xdel(dlq_stream_key, message_id)

                    logger.info(f"Retried DLQ task: {task_id}")
                    return True

            logger.warning(f"Task {task_id} not found in DLQ")
            return False

        except Exception as e:
            logger.error(f"Failed to retry DLQ task {task_id}: {e}")
            return False


# ========================================
# Factory Functions
# ========================================


def create_queue_service(
    task_class: type[T],
    stream_key: str,
    queue_name: str,
) -> QueueService[T]:
    """
    Factory function to create or get queue service instance.

    Args:
        task_class: Task class implementing ProducerTaskProtocol
        stream_key: Redis stream key (default: from config)
        queue_name: Logical queue name for identification

    Returns:
        QueueService singleton instance

    """
    return QueueService.get_instance(
        task_class=task_class,
        stream_key=stream_key,
        queue_name=queue_name,
    )


def get_queue_service_by_name(queue_name: str) -> QueueService:
    """
    Get queue service by queue name.

    Automatically discovers the queue from Redis if it exists.

    Args:
        queue_name: Queue identifier (transcription, tts, agent, etc.)

    Returns:
        QueueService instance for the specified queue

    Raises:
        ValueError: If queue is not found in Redis
    """
    # Map queue names to task classes
    # Only need to map for known types, new types can be added here
    _task_class_map = {
        "transcription": TranscriptionTask,
        "save_transcription": SaveTranscriptionTask,
    }

    # Check if queue name is registered
    if queue_name not in _task_class_map:
        raise ValueError(f"Queue '{queue_name}' not found. Available queues: {', '.join(_task_class_map.keys())}")

    # Construct stream key from queue name
    stream_key = f"{queue_name}:stream"

    # Get task class
    task_class = _task_class_map[queue_name]

    return create_queue_service(
        task_class=task_class,
        stream_key=stream_key,
        queue_name=queue_name,
    )
