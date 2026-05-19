"""
Room Registry Service - Singleton for managing active rooms

Now backed by Redis via Repository pattern.
Delegates all operations to RoomRegistryRepository.
"""

from typing import Optional, Dict

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.redis.room_registry_repository import (
    RoomRegistryRepository,
)
from orchestrator_service.services.redis.connection_pool import get_connection_manager

logger = get_logger(__name__)


class RoomRegistry:
    """
    Service layer for room registry management.

    Architecture:
    - Service Layer (this class): Business logic, validation
    - Repository Layer: Data access via RoomRegistryRepository
    - Infrastructure Layer: Redis connection via BaseHashRepository

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

        # Initialize repository immediately (uses shared Redis pool)
        self._repository = RoomRegistryRepository()
        self._initialized = True
        logger.info("RoomRegistry initialized (Repository pattern)")

    def _get_repository(self) -> RoomRegistryRepository:
        """Get repository instance."""
        return self._repository

    async def register_room(self, room_name: str, room_id: str) -> bool:
        """
        Register a room in the registry.

        Args:
            room_name: Name of the room to register
            room_id: str of the room

        Returns:
            True if registration is successful, False if the room already exists

        Raises:
            ConnectionError: If Redis operation fails
        """
        try:
            room_id_str = str(room_id)
            if not room_id_str or room_id_str == "None":
                logger.error(
                    f"Cannot register room '{room_name}': room_id converts to invalid string '{room_id_str}'"
                )
                return False
        except Exception as e:
            logger.error(
                f"Cannot register room '{room_name}': failed to convert room_id to string: {e}"
            )
            return False

        repository = self._get_repository()
        return await repository.register_room(room_name, room_id_str)

    async def unregister_room(self, room_name: str) -> bool:
        """
        Unregister a room from the registry.

        Args:
            room_name: name of the room to unregister

        Returns:
            True if unregistration is successful, False if the room does not exist

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        return await repository.unregister_room(room_name)

    async def is_registered(self, room_name: str) -> bool:
        """
        Check if a room is registered.

        Args:
            room_name: Name of the room to check

        Returns:
            True if the room is registered, False otherwise

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        return await repository.is_registered(room_name)

    async def get_room_id(self, room_name: str) -> Optional[str]:
        """
        Get the room_id of a room.

        Args:
            room_name: Name of the room

        Returns:
            Room ID string or None if the room does not exist

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        room_id_str = await repository.get_room_id(room_name)

        if not room_id_str:
            logger.debug(f"Room '{room_name}' not registered")
            return None

        # Validate the string format before converting
        if room_id_str == "None" or room_id_str == "null":
            logger.error(
                f"Room '{room_name}' has invalid room_id in Redis: '{room_id_str}'"
            )
            return None

        return room_id_str

    async def list_rooms(self) -> Dict[str, str]:
        """
        Get a list of all active rooms.

        Returns:
            Dictionary {room_name: room_id}

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        return await repository.list_rooms()

    async def count_rooms(self) -> int:
        """
        Count the number of active rooms.

        Returns:
            Number of rooms

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        return await repository.count_rooms()

    async def clear_all(self) -> int:
        """
        Clear all rooms (used for testing or cleanup).

        Returns:
            Number of rooms cleared

        Raises:
            ConnectionError: If Redis operation fails
        """
        repository = self._get_repository()
        return await repository.clear_all_rooms()

    async def get_stats(self) -> Dict:
        """
        Get registry statistics.

        Returns:
            Dictionary with statistics
        """
        repository = self._get_repository()
        return await repository.get_registry_stats()

    async def health_check(self) -> bool:
        """
        Check if Redis backend is healthy.

        Returns:
            True if healthy, False otherwise
        """
        if self._repository is None:
            return False

        connection_manager = get_connection_manager()
        return await connection_manager.health_check()


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
