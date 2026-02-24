"""
Generic Redis Producer Service for sending tasks to Redis Stream.

This service allows producing tasks directly to Redis Stream using XADD.
Supports any task type implementing ProducerTaskProtocol.
"""

import redis.asyncio as redis
from typing import ClassVar, Dict, Any, Generic, Optional, Type, TypeVar

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import RedisConfig
from orchestrator_service.models.stream_base import (
    ProducerTaskProtocol,
    StreamTaskStatus,
)

logger = get_logger(__name__)

# Type variable bound to ProducerTaskProtocol
T = TypeVar('T', bound=ProducerTaskProtocol)


class RedisProducerService(Generic[T]):
    """
    Generic Redis Stream producer for any task type.
    
    Type parameter T: Task type (must implement ProducerTaskProtocol)
    
    Sends tasks to Redis Stream using XADD, compatible with any consumer
    that reads from the same stream.
    
    Example:
        from orchestrator_service.models.transcription_task import TranscriptionTask
        
        producer = RedisProducerService[TranscriptionTask](config, TranscriptionTask)
        
        task = TranscriptionTask(
            egress_id="EG_xxx",
            filename="recording.ogg",
            ...
        )
        task_id = await producer.enqueue(task)
    """
    
    # Singleton registry per (task_class, stream_key)
    _instances: ClassVar[Dict[str, 'RedisProducerService']] = {}
    
    def __init__(self, config: RedisConfig, task_class: Type[T]):
        self._config = config
        self._task_class = task_class
        self._redis: Optional[redis.Redis] = None
        self._connected = False
        
        logger.info(
            f"RedisProducerService[{task_class.__name__}] created - "
            f"stream='{config.stream_key}'"
        )
    
    @classmethod
    def get_instance(
        cls,
        config: RedisConfig,
        task_class: Type[T],
    ) -> 'RedisProducerService[T]':
        """Get or create singleton instance for task class and stream."""
        instance_key = f"{task_class.__name__}:{config.stream_key}"
        
        if instance_key not in cls._instances:
            cls._instances[instance_key] = cls(config, task_class)
        
        return cls._instances[instance_key]
    
    async def connect(self) -> None:
        """Establish connection to Redis."""
        if self._connected and self._redis:
            return
        
        try:
            self._redis = redis.Redis(
                host=self._config.host,
                port=self._config.port,
                password=self._config.password or None,
                db=self._config.db,
                decode_responses=False,  # Keep bytes for compatibility
                socket_timeout=self._config.socket_timeout,
                socket_connect_timeout=self._config.socket_connect_timeout,
                max_connections=self._config.max_connections,
            )
            
            # Test connection
            await self._redis.ping()
            self._connected = True
            logger.info(
                f"✓ Connected to Redis: {self._config.host}:{self._config.port}"
            )
            
        except Exception as e:
            logger.error(f"✗ Failed to connect to Redis: {e}")
            self._connected = False
            raise
    
    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
            self._connected = False
            logger.info("Redis connection closed")
    
    async def enqueue(self, task: T) -> str:
        """
        Add a task to the Redis Stream.
        
        Args:
            task: Task object implementing ProducerTaskProtocol
        
        Returns:
            task_id: Unique task identifier
        
        Raises:
            ConnectionError: If not connected to Redis
        """
        if not self._redis or not self._connected:
            await self.connect()
        
        # Get task data from object
        task_data = task.to_dict()
        task_id = task.task_id
        
        try:
            # XADD to stream
            message_id = await self._redis.xadd(
                self._config.stream_key,
                task_data,
                maxlen=100000,  # Limit stream size
                approximate=True
            )
            
            message_id_str = message_id.decode() if isinstance(message_id, bytes) else str(message_id)
            
            # Store task metadata for quick lookup
            await self._redis.hset(
                f"{self._config.tasks_prefix}:{task_id}",
                mapping={
                    **task_data,
                    "message_id": message_id_str,
                    "status": StreamTaskStatus.PENDING.value,
                }
            )
            
            # Set expiry for task metadata (7 days)
            await self._redis.expire(
                f"{self._config.tasks_prefix}:{task_id}",
                7 * 24 * 3600
            )
            
            # Update stats
            await self._redis.hincrby(self._config.stats_key, "total_enqueued", 1)
            
            logger.info(
                f"📥 Enqueued task {task_id} → message_id={message_id_str}"
            )
            
            return task_id
            
        except Exception as e:
            logger.error(f"✗ Failed to enqueue task {task_id}: {e}")
            raise
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics."""
        if not self._redis or not self._connected:
            return {}
        
        try:
            # Get stream length
            stream_len = await self._redis.xlen(self._config.stream_key)
            
            # Get stats
            stats_data = await self._redis.hgetall(self._config.stats_key)
            stats = {
                k.decode(): v.decode()
                for k, v in stats_data.items()
            } if stats_data else {}
            
            return {
                "stream_length": stream_len,
                "total_enqueued": int(stats.get("total_enqueued", 0)),
                "total_processed": int(stats.get("total_processed", 0)),
                "total_failed": int(stats.get("total_failed", 0)),
            }
            
        except Exception as e:
            logger.error(f"Error getting queue stats: {e}")
            return {}
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._connected and self._redis is not None
