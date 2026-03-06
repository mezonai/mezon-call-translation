"""
Room Registry Repository - Domain-specific repository for room management

Extends BaseHashRepository with room-specific business logic.
"""

from typing import Dict, Optional

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decorator import singleton
from .base_hash_repository import BaseHashRepository

logger = get_logger(__name__)


@singleton
class RoomRegistryRepository(BaseHashRepository):
    """
    Repository for managing room registrations in Redis.
    
    Domain Model:
        - Key: room_name (string)
        - Value: room_id (string)
    
    Redis Structure:
        Hash: "rooms:registry"
        Stats: "rooms:stats"
    
    Business Rules:
        - Room names must be unique
        - Cannot register same room twice
        - Unregistering non-existent room returns False
    """
    
    HASH_KEY = "rooms:registry"
    STATS_KEY = "rooms:stats"
    
    def __init__(self):
        """Initialize room registry repository."""
        super().__init__()
        logger.info("RoomRegistryRepository initialized")
    
    # Domain-specific methods with business semantics
    
    async def register_room(self, room_name: str, room_id: str) -> bool:
        """
        Register a room.
        
        Business logic: A room can only be registered once.
        
        Args:
            room_name: Unique room name
            room_id: Associated room ID
        
        Returns:
            True if registered successfully, False if already exists
        """
        success = await self.set(self.HASH_KEY, self.STATS_KEY, room_name, room_id)
        
        if success:
            # Update domain-specific stats
            await self._increment_stat(self.STATS_KEY, "total_registered")
            await self._update_stat(self.STATS_KEY, "last_registered_at", 
                                   str(__import__('time').time()))
            logger.info(f"✅ Room '{room_name}' registered with ID '{room_id}'")
        else:
            logger.warning(f"⚠️ Room '{room_name}' already registered")
        
        return success
    
    async def unregister_room(self, room_name: str) -> bool:
        """
        Unregister a room.
        
        Business logic: Can only unregister existing rooms.
        
        Args:
            room_name: Room name to unregister
        
        Returns:
            True if unregistered, False if not found
        """
        # Get room_id before deletion for logging
        room_id = await self.get_room_id(room_name)
        
        success = await self.delete(self.HASH_KEY, self.STATS_KEY, room_name)
        
        if success:
            # Update domain-specific stats
            await self._increment_stat(self.STATS_KEY, "total_unregistered")
            await self._update_stat(self.STATS_KEY, "last_unregistered_at", 
                                   str(__import__('time').time()))
            logger.info(f"✅ Room '{room_name}' unregistered (room_id: {room_id})")
        else:
            logger.warning(f"⚠️ Room '{room_name}' not found in registry")
        
        return success
    
    async def is_registered(self, room_name: str) -> bool:
        """
        Check if a room is currently registered.
        
        Args:
            room_name: Room name to check
        
        Returns:
            True if registered, False otherwise
        """
        return await self.exists(self.HASH_KEY, room_name)
    
    async def get_room_id(self, room_name: str) -> Optional[str]:
        """
        Get the room ID for a registered room.
        
        Args:
            room_name: Room name
        
        Returns:
            Room ID string or None if not registered
        """
        return await self.get(self.HASH_KEY, room_name)
    
    async def list_rooms(self) -> Dict[str, str]:
        """
        List all registered rooms.
        
        Returns:
            Dictionary {room_name: room_id}
        """
        return await self.get_all(self.HASH_KEY)
    
    async def count_rooms(self) -> int:
        """
        Count total number of registered rooms.
        
        Returns:
            Number of registered rooms
        """
        return await self.count(self.HASH_KEY)
    
    async def clear_all_rooms(self) -> int:
        """
        Clear all registered rooms.
        
        Warning: This removes all room registrations.
        
        Returns:
            Number of rooms cleared
        """
        count = await self.clear(self.HASH_KEY)
        logger.warning(f"🗑️ Cleared all {count} rooms from registry")
        return count
    
    async def get_registry_stats(self) -> Dict:
        """
        Get room registry statistics.
        
        Returns:
            Dictionary with registry-specific stats
        """
        base_stats = await self.get_stats(self.HASH_KEY, self.STATS_KEY)
        
        # Add domain-specific fields
        redis = await self._get_redis()
        registry_stats = await redis.hgetall(self.STATS_KEY)
        
        return {
            "active_rooms": base_stats.get("total_items", 0),
            "total_registered": int(registry_stats.get("total_registered", 0)),
            "total_unregistered": int(registry_stats.get("total_unregistered", 0)),
            "last_registered_at": registry_stats.get("last_registered_at"),
            "last_unregistered_at": registry_stats.get("last_unregistered_at"),
        }
