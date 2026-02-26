"""
Active Egress Repository - Domain-specific repository for active egress management

Extends BaseHashRepository with egress-specific business logic.
"""

from typing import Dict, Optional

from orchestrator_service.utils.logger import get_logger
from .base_hash_repository import BaseHashRepository

logger = get_logger(__name__)


class ActiveEgressRepository(BaseHashRepository):
    """
    Repository for managing active egresses in Redis.
    
    Domain Model:
        - Key: track_sid (string)
        - Value: egress_id (string)
    
    Redis Structure:
        Hash: "egresses:active"
        Stats: "egresses:stats"
    
    Business Rules:
        - Track SIDs must be unique
        - Cannot start recording same track twice
        - Stopping non-existent egress returns False
    """
    
    HASH_KEY = "egresses:active"
    STATS_KEY = "egresses:stats"
    
    def __init__(self):
        """Initialize active egress repository."""
        super().__init__()
        logger.info("ActiveEgressRepository initialized")
    
    # Domain-specific methods with business semantics
    
    async def add(self, track_sid: str, egress_id: str) -> bool:
        """
        Register a new active egress.
        
        Business logic: A track can only have one active egress.
        
        Args:
            track_sid: Unique track SID
            egress_id: LiveKit egress ID
        
        Returns:
            True if registered, False if already exists
        """
        result = await self.set(track_sid, egress_id)
        if result:
            logger.info(f"✓ Registered egress: {track_sid} → {egress_id}")
        else:
            logger.warning(f"⏭ Track {track_sid} already has active egress")
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
        Stop and remove an active egress.
        
        Args:
            track_sid: Track SID
        
        Returns:
            Egress ID that was removed, or None if not found
        """
        # Get egress_id before deletion
        egress_id = await self.get(track_sid)
        if egress_id is None:
            logger.warning(f"No active egress for track {track_sid}")
            return None
        
        # Delete the egress
        deleted = await self.delete(track_sid)
        if deleted:
            logger.info(f"✓ Stopped egress: {track_sid} (egress_id: {egress_id})")
            return egress_id
        else:
            logger.error(f"Failed to delete egress for track {track_sid}")
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
    
    async def get_all_active_egresses(self) -> Dict[str, str]:
        """
        Get all active egresses.
        
        Returns:
            Dictionary mapping track_sid → egress_id
        """
        return await self.get_all()
    
    async def get_active_count(self) -> int:
        """
        Get number of active egresses.
        
        Returns:
            Count of active egresses
        """
        return await self.count()
    
    async def stop_all_egresses(self) -> int:
        """
        Stop all active egresses (clear the hash).
        
        Returns:
            Number of egresses stopped
        """
        count = await self.clear()
        if count > 0:
            logger.info(f"✓ Stopped all {count} active egresses")
        return count


# Singleton instance
_active_egress_repo: Optional[ActiveEgressRepository] = None


def get_active_egress_repository() -> ActiveEgressRepository:
    """
    Get singleton instance of ActiveEgressRepository.
    
    Returns:
        ActiveEgressRepository instance
    """
    global _active_egress_repo
    
    if _active_egress_repo is None:
        _active_egress_repo = ActiveEgressRepository()
    
    return _active_egress_repo
