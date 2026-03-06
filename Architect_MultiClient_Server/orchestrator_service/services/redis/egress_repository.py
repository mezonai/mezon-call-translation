"""
Egress Repository - Room-scoped repository for active egress management

Uses per-room hash: room:{room_name}:tracks
Each room has its own hash with track_sid -> egress_id mappings.
"""
import time 
from typing import ClassVar, Dict, List, Optional

from orchestrator_service.utils.logger import get_logger
from .base_hash_repository import BaseHashRepository

logger = get_logger(__name__)


class EgressRepository(BaseHashRepository):
    """
    Room-scoped repository for managing active egresses in Redis.
    
    Domain Model:
        - Key: track_sid (string)
        - Value: egress_id (string)
    
    Redis Structure:
        Hash: "room:{room_name}:tracks"
    
    Business Rules:
        - Track SIDs must be unique within a room
        - Cannot start recording same track twice
        - Each room has its own isolated hash
    
    Key Format:
        Hash "room:roomA:tracks":
            "TR_xxx" -> "EG_yyy"
            "TR_zzz" -> "EG_www"
    """
    
    # Factory registry: room_name -> instance
    _instances: ClassVar[Dict[str, 'EgressRepository']] = {}
    
    HASH_KEY = None  # Set dynamically per room
    
    def __init__(self, room_name: str):
        """
        Initialize egress repository for a specific room.
        
        Args:
            room_name: Room name to scope this repository
        """
        self._room_name = room_name
        self.HASH_KEY = f"room:{room_name}:tracks"
        super().__init__()
        logger.debug(f"EgressRepository initialized for room '{room_name}'")
    
    @classmethod
    def get_instance(cls, room_name: str) -> 'EgressRepository':
        """
        Get or create repository instance for a room.
        
        Args:
            room_name: Room name
        
        Returns:
            EgressRepository instance for the specified room
        """
        if room_name not in cls._instances:
            cls._instances[room_name] = cls(room_name)
        return cls._instances[room_name]
    
    @classmethod
    def remove_instance(cls, room_name: str) -> bool:
        """
        Remove repository instance for a room (cleanup).
        
        Args:
            room_name: Room name
        
        Returns:
            True if removed, False if not found
        """
        if room_name in cls._instances:
            del cls._instances[room_name]
            logger.debug(f"Removed EgressRepository instance for room '{room_name}'")
            return True
        return False
    
    @property
    def room_name(self) -> str:
        """Get the room name this repository is scoped to."""
        return self._room_name
    
    # ==================== Core Operations ====================
    
    async def add(self, track_sid: str, egress_id: str) -> bool:
        """
        Register a new active egress.
        
        Args:
            track_sid: Unique track SID
            egress_id: LiveKit egress ID
        
        Returns:
            True if registered, False if already exists
        """
        result = await self.set(track_sid, egress_id)
        if result:
            logger.info(f"✓ Registered egress in room '{self._room_name}': {track_sid} → {egress_id}")
        else:
            logger.warning(f"⏭ Track {track_sid} in room '{self._room_name}' already has active egress")
        return result
    
    async def get_egress_id(self, track_sid: str) -> Optional[str]:
        """
        Get egress ID for a track.
        
        Args:
            track_sid: Track SID
        
        Returns:
            Egress ID or None if not found
        """
        return await self.get(track_sid)
    
    async def pop(self, track_sid: str) -> Optional[str]:
        """
        Remove an active egress and return its egress_id.
        
        Args:
            track_sid: Track SID
        
        Returns:
            Egress ID that was removed, or None if not found
        """
        # Get egress_id before deletion
        egress_id = await self.get(track_sid)
        if egress_id is None:
            # This is expected for non-audio tracks (video, screen share)
            logger.debug(f"No active egress for {track_sid} in room '{self._room_name}' (may be non-audio track)")
            return None
        
        # Delete the egress
        deleted = await self.delete(track_sid)
        if deleted:
            logger.info(f"✓ Removed egress in room '{self._room_name}': {track_sid} (egress_id: {egress_id})")
            return egress_id
        else:
            logger.error(f"Failed to delete egress for {track_sid} in room '{self._room_name}'")
            return None
    
    async def is_active(self, track_sid: str) -> bool:
        """
        Check if track has active egress.
        
        Args:
            track_sid: Track SID
        
        Returns:
            True if active, False otherwise
        """
        return await self.exists(track_sid)
    
    # ==================== Bulk Operations ====================
    
    async def get_all_tracks(self) -> Dict[str, str]:
        """
        Get all active egresses in this room.
        
        Returns:
            Dictionary mapping track_sid → egress_id
        """
        return await self.get_all()
    
    async def get_track_list(self) -> List[str]:
        """
        Get list of all track_sids in this room.
        
        Returns:
            List of track_sids
        """
        data = await self.get_all()
        return list(data.keys())
    
    async def get_active_count(self) -> int:
        """
        Get number of active egresses in this room.
        
        Returns:
            Count of active egresses
        """
        return await self.count()
    
    async def stop_all(self) -> int:
        """
        Clear all active egresses in this room.
        
        Returns:
            Number of egresses cleared
        """
        count = await self.clear()
        if count > 0:
            logger.info(f"✓ Cleared all {count} active egresses in room '{self._room_name}'")
        return count


# ========================================
# Factory Functions
# ========================================

def get_egress_repository(room_name: str) -> EgressRepository:
    """
    Factory function to get or create egress repository for a room.
    
    Args:
        room_name: Room name
    
    Returns:
        EgressRepository instance for the specified room
    """
    return EgressRepository.get_instance(room_name)
