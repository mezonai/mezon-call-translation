"""
Egress Repository - Singleton repository for active egress management

Uses per-room hash: room:{room_name}:tracks
Each room has its own hash with track_sid -> egress_id mappings.
"""

import time
from typing import Dict, List, Optional

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decorator import singleton
from .base_hash_repository import BaseHashRepository

logger = get_logger(__name__)


@singleton
class EgressRepository(BaseHashRepository):
    """
    Singleton repository for managing active egresses in Redis.

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

    def __init__(self):
        """Initialize egress repository singleton."""
        super().__init__()
        logger.debug("EgressRepository singleton initialized")

    # ==================== Core Operations ====================

    def _build_hash_key(self, room_name: str) -> str:
        """Build the Redis hash key for a given room."""
        return f"room:{room_name}:tracks"

    async def add(self, room_name: str, track_sid: str, egress_id: str) -> bool:
        """
        Register a new active egress.

        Args:
            room_name: Room name
            track_sid: Unique track SID
            egress_id: LiveKit egress ID

        Returns:
            True if registered, False if already exists
        """
        hash_key = self._build_hash_key(room_name)
        result = await self.set(hash_key, None, track_sid, egress_id)

        if result:
            logger.info(f"✓ Registered egress in room '{room_name}': {track_sid} → {egress_id}")
        else:
            logger.warning(f"⏭ Track {track_sid} in room '{room_name}' already has active egress")

        return result

    async def get_egress_id(self, room_name: str, track_sid: str) -> Optional[str]:
        """
        Get egress ID for a track.

        Args:
            room_name: Room name
            track_sid: Track SID

        Returns:
            Egress ID or None if not found
        """
        hash_key = self._build_hash_key(room_name)
        return await self.get(hash_key, track_sid)

    async def pop(self, room_name: str, track_sid: str) -> Optional[str]:
        """
        Remove an active egress and return its egress_id.

        Args:
            room_name: Room name
            track_sid: Track SID

        Returns:
            Egress ID that was removed, or None if not found
        """
        hash_key = self._build_hash_key(room_name)

        # Get egress_id before deletion
        egress_id = await self.get(hash_key, track_sid)
        if egress_id is None:
            # This is expected for non-audio tracks (video, screen share)
            logger.debug(f"No active egress for {track_sid} in room '{room_name}' (may be non-audio track)")
            return None

        # Delete the egress
        deleted = await self.delete(hash_key, None, track_sid)
        if deleted:
            logger.info(f"✓ Removed egress in room '{room_name}': {track_sid} (egress_id: {egress_id})")
            return egress_id
        else:
            logger.error(f"Failed to delete egress for {track_sid} in room '{room_name}'")
            return None

    async def is_active(self, room_name: str, track_sid: str) -> bool:
        """
        Check if track has active egress.

        Args:
            room_name: Room name
            track_sid: Track SID

        Returns:
            True if active, False otherwise
        """
        hash_key = self._build_hash_key(room_name)
        return await self.exists(hash_key, track_sid)

    # ==================== Bulk Operations ====================

    async def get_all_tracks(self, room_name: str) -> Dict[str, str]:
        """
        Get all active egresses in a room.

        Args:
            room_name: Room name

        Returns:
            Dictionary mapping track_sid → egress_id
        """
        hash_key = self._build_hash_key(room_name)
        return await self.get_all(hash_key)

    async def get_track_list(self, room_name: str) -> List[str]:
        """
        Get list of all track_sids in a room.

        Args:
            room_name: Room name

        Returns:
            List of track_sids
        """
        data = await self.get_all_tracks(room_name)
        return list(data.keys())

    async def get_active_count(self, room_name: str) -> int:
        """
        Get number of active egresses in a room.

        Args:
            room_name: Room name

        Returns:
            Count of active egresses
        """
        hash_key = self._build_hash_key(room_name)
        return await self.count(hash_key)

    async def stop_all(self, room_name: str) -> int:
        """
        Clear all active egresses in a room.

        Args:
            room_name: Room name

        Returns:
            Number of egresses cleared
        """
        hash_key = self._build_hash_key(room_name)
        count = await self.clear(hash_key)

        if count > 0:
            logger.info(f"✓ Cleared all {count} active egresses in room '{room_name}'")

        return count
