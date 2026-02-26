"""
Base Hash Repository - Generic Redis Hash operations

Provides common CRUD operations for Redis Hash data structures.
Subclasses define domain-specific logic.
"""

from abc import ABC
from typing import Dict, Optional, Any
import time
from redis.asyncio import Redis
from orchestrator_service.utils.logger import get_logger
from .connection_pool import get_redis_connection

logger = get_logger(__name__)


class BaseHashRepository(ABC):
    """
    Base repository for Redis Hash operations.
    
    Provides generic CRUD operations:
    - set(key, value) → HSET
    - get(key) → HGET
    - delete(key) → HDEL
    - exists(key) → HEXISTS
    - get_all() → HGETALL
    - count() → HLEN
    - clear() → DEL
    
    Subclasses should define:
    - HASH_KEY: Redis hash key name
    - STATS_KEY: Redis stats key name (optional)
    
    Example:
        class RoomRegistryRepository(BaseHashRepository):
            HASH_KEY = "rooms:registry"
            STATS_KEY = "rooms:stats"
    """
    
    # Subclasses must override these
    HASH_KEY: str = None
    STATS_KEY: Optional[str] = None
    
    def __init__(self):
        """Initialize repository."""
        if self.HASH_KEY is None:
            raise ValueError(
                f"{self.__class__.__name__} must define HASH_KEY"
            )
        
        self._redis: Optional[Redis] = None
        logger.debug(f"{self.__class__.__name__} initialized")
    
    async def _get_redis(self) -> Redis:
        """
        Get Redis client, connect if needed.
        
        Returns:
            Redis client instance
        """
        if self._redis is None or self._redis.connection_pool is None:
            self._redis = await get_redis_connection()
        return self._redis
    
    async def set(self, key: str, value: str) -> bool:
        """
        Set a key-value pair in the hash.
        
        Args:
            key: Hash field key
            value: Value to store
        
        Returns:
            True if set successfully, False if key already exists
        """
        redis = await self._get_redis()
        
        try:
            # Check if exists first
            exists = await redis.hexists(self.HASH_KEY, key)
            if exists:
                logger.warning(
                    f"[{self.__class__.__name__}] Key '{key}' already exists"
                )
                return False
            
            # Set value
            await redis.hset(self.HASH_KEY, key, value)
            
            # Update stats if enabled
            if self.STATS_KEY:
                await self._increment_stat("total_set")
                await self._update_stat("last_set_at", str(time.time()))
            
            logger.info(
                f"[{self.__class__.__name__}] Set '{key}' = '{value}'"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to set '{key}': {e}"
            )
            raise
    
    async def get(self, key: str) -> Optional[str]:
        """
        Get value for a key.
        
        Args:
            key: Hash field key
        
        Returns:
            Value string or None if not found
        """
        redis = await self._get_redis()
        
        try:
            value = await redis.hget(self.HASH_KEY, key)
            return value
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get '{key}': {e}"
            )
            raise
    
    async def delete(self, key: str) -> bool:
        """
        Delete a key from the hash.
        
        Args:
            key: Hash field key
        
        Returns:
            True if deleted, False if not found
        """
        redis = await self._get_redis()
        
        try:
            # Get value before deletion (for logging)
            value = await redis.hget(self.HASH_KEY, key)
            
            if value is None:
                logger.warning(
                    f"[{self.__class__.__name__}] Key '{key}' not found"
                )
                return False
            
            # Delete
            deleted = await redis.hdel(self.HASH_KEY, key)
            
            if deleted > 0:
                # Update stats if enabled
                if self.STATS_KEY:
                    await self._increment_stat("total_deleted")
                    await self._update_stat("last_deleted_at", str(time.time()))
                
                logger.info(
                    f"[{self.__class__.__name__}] Deleted '{key}' "
                    f"(value: {value})"
                )
                return True
            else:
                return False
                
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to delete '{key}': {e}"
            )
            raise
    
    async def exists(self, key: str) -> bool:
        """
        Check if a key exists.
        
        Args:
            key: Hash field key
        
        Returns:
            True if exists, False otherwise
        """
        redis = await self._get_redis()
        
        try:
            exists = await redis.hexists(self.HASH_KEY, key)
            return bool(exists)
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to check '{key}': {e}"
            )
            raise
    
    async def get_all(self) -> Dict[str, str]:
        """
        Get all key-value pairs.
        
        Returns:
            Dictionary of all pairs
        """
        redis = await self._get_redis()
        
        try:
            data = await redis.hgetall(self.HASH_KEY)
            return dict(data) if data else {}
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get all: {e}"
            )
            raise
    
    async def count(self) -> int:
        """
        Count number of keys.
        
        Returns:
            Number of keys in hash
        """
        redis = await self._get_redis()
        
        try:
            count = await redis.hlen(self.HASH_KEY)
            return count
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to count: {e}"
            )
            raise
    
    async def clear(self) -> int:
        """
        Clear all keys.
        
        Returns:
            Number of keys cleared
        """
        redis = await self._get_redis()
        
        try:
            count = await self.count()
            if count > 0:
                await redis.delete(self.HASH_KEY)
                logger.info(
                    f"[{self.__class__.__name__}] Cleared {count} keys"
                )
            return count
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to clear: {e}"
            )
            raise
    
    async def get_stats(self) -> Dict[str, Any]:
        """
        Get repository statistics.
        
        Returns:
            Dictionary with stats
        """
        if not self.STATS_KEY:
            return {}
        
        redis = await self._get_redis()
        
        try:
            stats_data = await redis.hgetall(self.STATS_KEY)
            item_count = await self.count()
            
            return {
                "total_items": item_count,
                "total_set": int(stats_data.get("total_set", 0)),
                "total_deleted": int(stats_data.get("total_deleted", 0)),
                "last_set_at": stats_data.get("last_set_at"),
                "last_deleted_at": stats_data.get("last_deleted_at"),
            }
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get stats: {e}"
            )
            return {"error": str(e)}
    
    # Helper methods for stats
    
    async def _increment_stat(self, stat_name: str, amount: int = 1) -> None:
        """Increment a stat counter."""
        if not self.STATS_KEY:
            return
        
        redis = await self._get_redis()
        await redis.hincrby(self.STATS_KEY, stat_name, amount)
    
    async def _update_stat(self, stat_name: str, value: str) -> None:
        """Update a stat value."""
        if not self.STATS_KEY:
            return
        
        redis = await self._get_redis()
        await redis.hset(self.STATS_KEY, stat_name, value)
