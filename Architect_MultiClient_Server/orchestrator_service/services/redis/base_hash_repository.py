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
    
    
    Example:
        class RoomRegistryRepository(BaseHashRepository):
    """
    
    
    # Stats field names
    STAT_TOTAL_SET = "total_set"
    STAT_TOTAL_DELETED = "total_deleted"
    STAT_LAST_SET_AT = "last_set_at"
    STAT_LAST_DELETED_AT = "last_deleted_at"
    
    def __init__(self):
        """Initialize repository.""" 
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
    
    async def set(self, HASH_KEY: str, STATS_KEY: Optional[str], key: str, value: str) -> bool:
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
            exists = await redis.hexists(HASH_KEY, key)
            if exists:
                logger.warning(
                    f"[{self.__class__.__name__}] Key '{key}' already exists"
                )
                return False
            
            # Set value
            await redis.hset(HASH_KEY, key, value)

            # Update stats if enabled
            if STATS_KEY:
                await self._increment_stat(STATS_KEY, self.STAT_TOTAL_SET)
                await self._update_stat(STATS_KEY, self.STAT_LAST_SET_AT, str(time.time()))
            
            logger.info(
                f"[{self.__class__.__name__}] Set '{key}' = '{value}'"
            )
            return True
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to set '{key}': {e}"
            )
            raise
    
    async def get(self, HASH_KEY: str, key: str) -> Optional[str]:
        """
        Get value for a key.
        
        Args:
            key: Hash field key
        
        Returns:
            Value string or None if not found
        """
        redis = await self._get_redis()
        
        try:
            value = await redis.hget(HASH_KEY, key)
            return value
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get '{key}': {e}"
            )
            raise
    
    async def delete(self, HASH_KEY: str, STATS_KEY: Optional[str], key: str) -> bool:
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
            value = await redis.hget(HASH_KEY, key)
            
            if value is None:
                logger.warning(
                    f"[{self.__class__.__name__}] Key '{key}' not found"
                )
                return False
            
            # Delete
            deleted = await redis.hdel(HASH_KEY, key)
            
            if deleted > 0:
                # Update stats if enabled
                if STATS_KEY:
                    await self._increment_stat(STATS_KEY, self.STAT_TOTAL_DELETED)
                    await self._update_stat(STATS_KEY, self.STAT_LAST_DELETED_AT, str(time.time()))
                
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
    
    async def exists(self, HASH_KEY: str, key: str) -> bool:
        """
        Check if a key exists.
        
        Args:
            key: Hash field key
        
        Returns:
            True if exists, False otherwise
        """
        redis = await self._get_redis()
        
        try:
            exists = await redis.hexists(HASH_KEY, key)
            return bool(exists)
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to check '{key}': {e}"
            )
            raise
    
    async def get_all(self, HASH_KEY: str) -> Dict[str, str]:
        """
        Get all key-value pairs.
        
        Returns:
            Dictionary of all pairs
        """
        redis = await self._get_redis()
        
        try:
            data = await redis.hgetall(HASH_KEY)
            return dict(data) if data else {}
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get all: {e}"
            )
            raise
    
    async def count(self, HASH_KEY: str) -> int:
        """
        Count number of keys.
        
        Returns:
            Number of keys in hash
        """
        redis = await self._get_redis()
        
        try:
            count = await redis.hlen(HASH_KEY)
            return count
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to count: {e}"
            )
            raise
    
    async def clear(self, HASH_KEY: str) -> int:
        """
        Clear all keys.
        
        Returns:
            Number of keys cleared
        """
        redis = await self._get_redis()
        
        try:
            count = await self.count(HASH_KEY)
            if count > 0:
                await redis.delete(HASH_KEY)
                logger.info(
                    f"[{self.__class__.__name__}] Cleared {count} keys"
                )
            return count
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to clear: {e}"
            )
            raise
    
    async def get_stats(self, HASH_KEY: str, STATS_KEY: str) -> Dict[str, Any]:
        """
        Get repository statistics.
        
        Returns:
            Dictionary with stats
        """
        if not STATS_KEY:
            return {}
        
        redis = await self._get_redis()
        
        try:
            stats_data = await redis.hgetall(STATS_KEY)
            item_count = await self.count(HASH_KEY)
            
            return {
                "total_items": item_count,
                self.STAT_TOTAL_SET: int(stats_data.get(self.STAT_TOTAL_SET, 0)),
                self.STAT_TOTAL_DELETED: int(stats_data.get(self.STAT_TOTAL_DELETED, 0)),
                self.STAT_LAST_SET_AT: stats_data.get(self.STAT_LAST_SET_AT),
                self.STAT_LAST_DELETED_AT: stats_data.get(self.STAT_LAST_DELETED_AT),
            }
            
        except Exception as e:
            logger.error(
                f"[{self.__class__.__name__}] Failed to get stats: {e}"
            )
            return {"error": str(e)}
    
    # Helper methods for stats
    
    async def _increment_stat(self, STATS_KEY: str, stat_name: str, amount: int = 1) -> None:
        """Increment a stat counter."""

        redis = await self._get_redis()
        await redis.hincrby(STATS_KEY, stat_name, amount)
    
    async def _update_stat(self, STATS_KEY: str, stat_name: str, value: str) -> None:
        """Update a stat value."""
        
        redis = await self._get_redis()
        await redis.hset(STATS_KEY, stat_name, value)
