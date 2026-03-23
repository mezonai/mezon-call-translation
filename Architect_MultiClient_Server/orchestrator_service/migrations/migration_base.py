"""
Base class for MongoDB migrations
"""
from abc import ABC, abstractmethod
from typing import Optional
from motor.motor_asyncio import AsyncIOMotorDatabase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class MigrationBase(ABC):
    """Base class for all migrations"""

    def __init__(self, db: AsyncIOMotorDatabase):
        self.db = db

    @property
    @abstractmethod
    def migration_id(self) -> str:
        """Unique identifier for this migration (e.g., '001_create_metadata_events_indexes')"""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description of what this migration does"""
        pass

    @abstractmethod
    async def up(self) -> bool:
        """
        Apply the migration (forward).

        Returns:
            True if successful, False otherwise
        """
        pass

    @abstractmethod
    async def down(self) -> bool:
        """
        Rollback the migration (backward).

        Returns:
            True if successful, False otherwise
        """
        pass

    async def is_applied(self) -> bool:
        """
        Check if this migration has already been applied.

        Returns:
            True if migration exists in migrations collection, False otherwise
        """
        try:
            migrations_collection = self.db["migrations"]
            result = await migrations_collection.find_one({"migration_id": self.migration_id})
            return result is not None
        except Exception as e:
            logger.error(f"Failed to check migration status: {e}")
            return False

    async def mark_applied(self) -> bool:
        """Mark this migration as applied in the database"""
        try:
            from datetime import datetime
            migrations_collection = self.db["migrations"]
            await migrations_collection.insert_one({
                "migration_id": self.migration_id,
                "description": self.description,
                "applied_at": datetime.utcnow()
            })
            logger.info(f"✅ Marked migration as applied: {self.migration_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark migration as applied: {e}")
            return False

    async def mark_reverted(self) -> bool:
        """Remove this migration from applied migrations"""
        try:
            migrations_collection = self.db["migrations"]
            await migrations_collection.delete_one({"migration_id": self.migration_id})
            logger.info(f"✅ Marked migration as reverted: {self.migration_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to mark migration as reverted: {e}")
            return False
