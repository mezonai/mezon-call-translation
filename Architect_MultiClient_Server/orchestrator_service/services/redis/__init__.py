"""
Redis infrastructure layer

Provides base classes and utilities for Redis operations.
"""

from .base_hash_repository import BaseHashRepository
from .room_registry_repository import RoomRegistryRepository
from .connection_pool import get_redis_connection

__all__ = [
    'BaseHashRepository',
    'RoomRegistryRepository',
    'get_redis_connection',
]
