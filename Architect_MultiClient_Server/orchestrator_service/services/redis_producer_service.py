"""
Redis Producer Service for sending transcription tasks directly to Redis Stream.

This service allows the orchestrator to bypass the STT API and enqueue tasks
directly into the Redis Stream that STT workers consume from.
"""

import time
import uuid
import redis.asyncio as redis
from typing import Optional, Dict, Any
from dataclasses import dataclass

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import RedisConfig

logger = get_logger(__name__)


class RedisProducerService:
    """
    Redis Stream producer for transcription tasks.
    
    Sends tasks directly to Redis Stream using XADD, matching the format
    expected by STT service's RedisStreamService consumer.
    """
    
    def __init__(self, config: RedisConfig):
        self._config = config
        self._redis: Optional[redis.Redis] = None
        self._connected = False
    
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
    
    async def enqueue(
        self,
        egress_id: str,
        filename: str,
        location: str,
        duration: str,
        started_at: str,
        ended_at: str,
        source: Optional[str] = None,
        priority: int = 5,  # TaskPriority.NORMAL = 5
    ) -> str:
        """
        Add a transcription task to the Redis Stream.
        
        Args:
            egress_id: LiveKit egress ID
            filename: Path to audio file in MinIO
            location: Full file location (bucket/path)
            duration: Recording duration
            started_at: Recording start timestamp
            ended_at: Recording end timestamp
            source: Optional source identifier (e.g., MICROPHONE, SCREEN_SHARE)
            priority: Task priority (1-9, lower = higher priority)
        
        Returns:
            task_id: Unique task identifier
        
        Raises:
            ConnectionError: If not connected to Redis
        """
        if not self._redis or not self._connected:
            await self.connect()
        
        # Generate unique task ID (matching STT service format)
        task_id = f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}"
        
        # Build task data (matching STT service's StreamTask format)
        task_data = {
            "task_id": task_id,
            "filename": filename,
            "egress_id": egress_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration": duration,
            "location": location,
            "source": source or "",
            "priority": str(priority),
            "created_at": str(time.time()),
            "retry_count": "0",
        }
        
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
                    "status": "pending",
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
            logger.error(f"✗ Failed to enqueue task: {e}")
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
