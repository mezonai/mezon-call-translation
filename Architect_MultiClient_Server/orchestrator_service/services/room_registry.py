"""
Room Registry Service - Singleton for managing active rooms
"""
from typing import Optional, Dict
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class RoomRegistry:
    """
    Singleton service save and manage active rooms.
    Only registered rooms are allowed to process webhook events.
    """
    
    _instance: Optional["RoomRegistry"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._rooms: Dict[str, str] = {}  # {room_name: room_id}
        self._initialized = True
        logger.info("RoomRegistry initialized")
    
    def register_room(self, room_name: str, room_id: str) -> bool:
        """
        Register a room in the registry.
        
        Args:
            room_name: Name of the room to register
            room_id: ID of the room 
            
        Returns:
            True if registration is successful, False if the room already exists
        """
        if room_name in self._rooms:
            logger.warning(f"Room '{room_name}' already registered")
            return False
        
        self._rooms[room_name] = room_id
        logger.info(f"Room '{room_name}' registered at {room_id}")
        return True
    
    def unregister_room(self, room_name: str) -> bool:
        """
        Register a room in the registry.
        
        Args:
            room_name: name of the room to unregister
            
        Returns:
            True if unregistration is successful, False if the room does not exist
        """
        if room_name not in self._rooms:
            logger.warning(f"Room '{room_name}' not found in registry")
            return False
        
        room_id = self._rooms.pop(room_name)
        logger.info(f"Room '{room_name}' unregistered (room_id: {room_id})")
        return True
    
    def is_registered(self, room_name: str) -> bool:
        """
        Check if a room is registered.
        
        Args:
            room_name: Name of the room to check
            
        Returns:
            True if the room is registered, False otherwise
        """
        return room_name in self._rooms
    
    def get_room_id(self, room_name: str) -> Optional[str]:
        """
        Get the room_id of a room.
        
        Args:
            room_name: Name of the room
            
        Returns:
            room_id string or None if the room does not exist
        """
        return self._rooms.get(room_name)
    
    def list_rooms(self) -> Dict[str, str]:
        """
        Get a list of all active rooms.
        
        Returns:
            Dictionary {room_name: room_id}
        """
        return self._rooms.copy()
    
    def count_rooms(self) -> int:
        """
        Count the number of active rooms.
        
        Returns:
            Number of rooms
        """
        return len(self._rooms)
    
    def clear_all(self):
        """Clear all rooms (used for testing or cleanup)"""
        count = len(self._rooms)
        self._rooms.clear()
        logger.info(f"Cleared all {count} rooms from registry")


# Global singleton instance
_room_registry: Optional[RoomRegistry] = None


def get_room_registry() -> RoomRegistry:
    """
    Get the global room registry instance.
    
    Returns:
        RoomRegistry singleton instance
    """
    global _room_registry
    if _room_registry is None:
        _room_registry = RoomRegistry()
    return _room_registry
