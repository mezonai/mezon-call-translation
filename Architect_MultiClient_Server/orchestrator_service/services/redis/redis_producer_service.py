"""
Generic Redis Producer Service for sending tasks to Redis Stream.

This service allows producing tasks directly to Redis Stream using XADD.
Supports any task type implementing ProducerTaskProtocol.
Uses the shared Redis connection pool (RedisConnectionManager).
"""
from redis.asyncio import Redis
from typing import ClassVar, Dict, Any, Generic, Optional, Type, TypeVar

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decode import decode_value, decode_mapping
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.redis.connection_pool import get_connection_manager
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
    """
    
    # Singleton registry per (task_class, stream_key)
    _instances: ClassVar[Dict[str, 'RedisProducerService']] = {}
    
    def __init__(
        self,
        task_class: Type[T],
        stream_key: Optional[str] = None,
    ):
        """
        Initialize Redis Producer service.
        
        Args:
            task_class: Task class implementing ProducerTaskProtocol
            stream_key: Redis stream key (default: from config)
        """
        self._task_class = task_class
        self._config = get_config().redis
        self._redis: Optional[Redis] = None
        
        # Keys - use provided value or fall back to config
        self._stream_key = stream_key
        self._tasks_prefix = f"{self._stream_key}:tasks"
        self._stats_key = f"{self._stream_key}:stats"
        
        logger.info(
            f"RedisProducerService[{task_class.__name__}] created - "
            f"stream='{self._stream_key}'"
        )
    
    @classmethod
    def get_instance(
        cls,
        task_class: Type[T],
        stream_key: Optional[str] = None,
    ) -> 'RedisProducerService[T]':
        """
        Get or create singleton instance for task class and stream.
        
        Args:
            task_class: Task class implementing ProducerTaskProtocol
            stream_key: Redis stream key (default: from config)
        
        Returns:
            RedisProducerService instance for the specified configuration
        """
        effective_stream_key = stream_key
        instance_key = f"{task_class.__name__}:{effective_stream_key}"
        
        if instance_key not in cls._instances:
            cls._instances[instance_key] = cls(
                task_class=task_class,
                stream_key=stream_key,
            )
        
        return cls._instances[instance_key]
    
    async def connect(self) -> None:
        """
        Establish connection to Redis using connection pool.
        
        Raises:
            ConnectionError: If cannot connect to Redis
        """
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
                f"✅ Redis producer using shared pool at {self._config.host}:{self._config.port}"
            )

        except Exception as e:
            logger.error(f"✗ Failed to connect to Redis: {e}")
            self._redis = None
            raise ConnectionError(f"Redis connection failed: {e}")
    
    async def close(self) -> None:
        """Release this client's connections back to the shared pool (does not tear down the pool)."""
        if self._redis:
            await self._redis.close()
            self._redis = None
        logger.debug("Redis producer client released to shared pool")
    
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
        if not self._redis:
            await self.connect()
        
        # Get task data from object
        task_data = task.to_dict()
        task_id = task.task_id
        
        try:
            # XADD to stream
            message_id = await self._redis.xadd(
                self._stream_key,
                task_data,
                maxlen=100000,  # Limit stream size
                approximate=True
            )
            
            message_id_str = decode_value(message_id)
            
            # Store task metadata for quick lookup
            await self._redis.hset(
                f"{self._tasks_prefix}:{task_id}",
                mapping={
                    **task_data,
                    "message_id": message_id_str,
                    "status": StreamTaskStatus.PENDING.value,
                }
            )
            
            # Set expiry for task metadata (7 days)
            await self._redis.expire(
                f"{self._tasks_prefix}:{task_id}",
                7 * 24 * 3600
            )
            
            # Update stats
            await self._redis.hincrby(self._stats_key, "total_enqueued", 1)
            
            logger.info(
                f"📥 Enqueued task {task_id} → message_id={message_id_str}"
            )
            
            return task_id
            
        except Exception as e:
            logger.error(f"✗ Failed to enqueue task {task_id}: {e}")
            raise
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """Get queue statistics including active workers count."""
        if not self._redis:
            return {}
        
        try:
            # Get stream length
            stream_len = await self._redis.xlen(self._stream_key)
            
            # Get stats
            stats_data = await self._redis.hgetall(self._stats_key)
            stats = decode_mapping(stats_data) if stats_data else {}
            
            # Count active workers
            workers_key = f"{self._stream_key}:workers"
            active_workers_count = await self._redis.hlen(workers_key)
            
            return {
                "stream_key": self._stream_key,
                "stream_length": stream_len,
                "total_enqueued": int(stats.get("total_enqueued", 0)),
                "total_processed": int(stats.get("total_processed", 0)),
                "total_failed": int(stats.get("total_failed", 0)),
                "active_workers": active_workers_count,
            }
            
        except Exception as e:
            logger.error(f"Error getting queue stats: {e}")
            return {}
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to Redis."""
        return self._redis is not None
    
    @property
    def stream_key(self) -> str:
        """Get the stream key."""
        return self._stream_key
    
    @property
    def task_class(self) -> Type[T]:
        """Get the task class."""
        return self._task_class


# ========================================
# Factory Functions
# ========================================

def create_producer_service(
    task_class: Type[T],
    stream_key: str,
) -> RedisProducerService[T]:
    """
    Factory function to create or get producer service instance.
    
    Args:
        task_class: Task class implementing ProducerTaskProtocol
        stream_key: Redis stream key (default: from config)
    
    Returns:
        RedisProducerService singleton instance
    """
    return RedisProducerService.get_instance(
        task_class=task_class,
        stream_key=stream_key,
    )

