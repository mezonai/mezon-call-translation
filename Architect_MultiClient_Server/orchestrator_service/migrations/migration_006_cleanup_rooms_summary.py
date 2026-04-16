"""
Migration: Clean up rooms_summary documents

This migration:
- Removes the metadata field when it is null
- Converts room_id from string to ObjectId when possible
"""
from typing import Any

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CleanupRoomsSummary(MigrationBase):
    """Clean up rooms_summary documents and normalize room_id type."""

    BATCH_SIZE = 200

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db)

    @property
    def migration_id(self) -> str:
        return "006_cleanup_rooms_summary"

    @property
    def description(self) -> str:
        return "Remove null metadata from rooms_summary and convert room_id strings to ObjectId"

    @staticmethod
    def _to_object_id(value: Any) -> ObjectId | None:
        if isinstance(value, ObjectId):
            return value

        if isinstance(value, str):
            try:
                return ObjectId(value)
            except Exception:
                return None

        return None

    async def up(self) -> bool:
        """Apply the cleanup migration."""
        try:
            summary_collection = self.db["rooms_summary"]

            null_metadata_result = await summary_collection.update_many(
                {"metadata": {"$type": 10}},
                {"$unset": {"metadata": ""}},
            )
            logger.info(
                f"✅ Removed null metadata from {null_metadata_result.modified_count} rooms_summary documents"
            )

            cursor = summary_collection.find(
                {"room_id": {"$type": "string"}},
                {"_id": 1, "room_id": 1},
            ).batch_size(self.BATCH_SIZE)

            converted_count = 0
            skipped_count = 0

            async for document in cursor:
                room_id = document.get("room_id")
                object_id = self._to_object_id(room_id)

                if object_id is None:
                    skipped_count += 1
                    logger.warning(
                        f"⚠️  Skipping rooms_summary document {document.get('_id')} with invalid room_id: {room_id}"
                    )
                    continue

                await summary_collection.update_one(
                    {"_id": document["_id"]},
                    {"$set": {"room_id": object_id}},
                )
                converted_count += 1

                if converted_count % self.BATCH_SIZE == 0:
                    logger.info(f"📈 Converted {converted_count} rooms_summary room_id values so far")

            logger.info(f"✅ Converted {converted_count} rooms_summary room_id values to ObjectId")
            if skipped_count > 0:
                logger.warning(f"⚠️  Skipped {skipped_count} rooms_summary documents with invalid room_id values")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to clean up rooms_summary: {e}")
            return False

    async def down(self) -> bool:
        """Best-effort rollback for the cleanup migration."""
        try:
            summary_collection = self.db["rooms_summary"]

            cursor = summary_collection.find(
                {"room_id": {"$type": "objectId"}},
                {"_id": 1, "room_id": 1},
            ).batch_size(self.BATCH_SIZE)

            reverted_count = 0

            async for document in cursor:
                room_id = document.get("room_id")
                await summary_collection.update_one(
                    {"_id": document["_id"]},
                    {"$set": {"room_id": str(room_id)}},
                )
                reverted_count += 1

            logger.info(f"✅ Reverted {reverted_count} rooms_summary room_id values back to string")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to rollback rooms_summary cleanup: {e}")
            return False