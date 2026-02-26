"""
Shared Redis Connection Pool

Provides singleton Redis connection pool for all repositories.
"""

import redis.asyncio as redis
from redis.asyncio import ConnectionPool, Redis
from typing import Optional

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)


class RedisConnectionManager:
    """
    Singleton manager for Redis connection pool.
    
    All repositories share the same connection pool for efficiency.
    """
    
    _instance: Optional['RedisConnectionManager'] = None
    
    def __init__(self):
        """Initialize connection manager."""
        self._config = get_config().redis
        self._pool: Optional[ConnectionPool] = None
        self._connected = False
        
        logger.info("RedisConnectionManager initialized")
    
    @classmethod
    def get_instance(cls) -> 'RedisConnectionManager':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton (for testing)."""
        cls._instance = None
    
    async def connect(self) -> None:
        """
        Create connection pool.
        
        Raises:
            ConnectionError: If cannot connect to Redis
        """
        if self._connected and self._pool is not None:
            logger.debug("Redis connection pool already created")
            return
        
        try:
            self._pool = ConnectionPool(
                host=self._config.host,
                port=self._config.port,
                password=self._config.password or None,
                db=self._config.db,
                max_connections=self._config.max_connections,
                socket_timeout=self._config.socket_timeout,
                socket_connect_timeout=self._config.socket_connect_timeout,
                decode_responses=True,
            )
            
            # Test connection
            redis_client = Redis(connection_pool=self._pool)
            await redis_client.ping()
            await redis_client.close()
            
            self._connected = True
            logger.info(
                f"✅ Redis connection pool created: "
                f"{self._config.host}:{self._config.port}"
            )
            
        except Exception as e:
            logger.error(f"✗ Failed to create Redis connection pool: {e}")
            self._pool = None
            self._connected = False
            raise ConnectionError(f"Redis connection failed: {e}")
    
    async def disconnect(self) -> None:
        """Close connection pool."""
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        
        self._connected = False
        logger.info("Redis connection pool closed")
    
    def get_client(self) -> Redis:
        """
        Get a Redis client from the pool.
        
        Returns:
            Redis client instance
        
        Raises:
            RuntimeError: If connection pool not initialized
        """
        if not self._pool:
            raise RuntimeError(
                "Redis connection pool not initialized. "
                "Call await connect() first."
            )
        return Redis(connection_pool=self._pool)
    
    @property
    def is_connected(self) -> bool:
        """Check if connection pool is active."""
        return self._connected and self._pool is not None
    
    async def health_check(self) -> bool:
        """
        Check if Redis connection is healthy.
        
        Returns:
            True if healthy, False otherwise
        """
        if not self.is_connected:
            return False
        
        try:
            client = self.get_client()
            await client.ping()
            await client.close()
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


# Singleton instance
_manager: Optional[RedisConnectionManager] = None


async def get_redis_connection() -> Redis:
    """
    Get Redis client from shared connection pool.
    
    Auto-connects if not already connected.
    
    Returns:
        Redis client instance
    
    Raises:
        ConnectionError: If cannot connect to Redis
    """
    global _manager
    
    if _manager is None:
        _manager = RedisConnectionManager.get_instance()
    
    if not _manager.is_connected:
        await _manager.connect()
    
    return _manager.get_client()


def get_connection_manager() -> RedisConnectionManager:
    """
    Get singleton connection manager.
    
    Returns:
        RedisConnectionManager instance
    """
    global _manager
    
    if _manager is None:
        _manager = RedisConnectionManager.get_instance()
    
    return _manager
