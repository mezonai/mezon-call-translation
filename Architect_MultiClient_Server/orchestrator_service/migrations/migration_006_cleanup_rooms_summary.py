"""
Migration: Clean up rooms_summary documents

This migration:
- Removes the metadata field when it is null
- Converts room_id from string to ObjectId when possible
"""
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CleanupRoomsSummary(MigrationBase):
    """Clean up rooms_summary documents and normalize room_id type."""

    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db)

    @property
    def migration_id(self) -> str:
        return "006_cleanup_rooms_summary"

    @property
    def description(self) -> str:
        return "Remove null metadata from rooms_summary and convert room_id strings to ObjectId"

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

            valid_string_filter = {
                "room_id": {
                    "$type": "string",
                    "$regex": "^[a-fA-F0-9]{24}$",
                }
            }
            invalid_string_filter = {
                "room_id": {
                    "$type": "string",
                    "$not": {"$regex": "^[a-fA-F0-9]{24}$"},
                }
            }

            skipped_count = await summary_collection.count_documents(invalid_string_filter)
            if skipped_count > 0:
                logger.warning(
                    f"⚠️  Found {skipped_count} rooms_summary documents with invalid room_id string values"
                )

            converted_result = await summary_collection.update_many(
                valid_string_filter,
                [{"$set": {"room_id": {"$toObjectId": "$room_id"}}}],
            )
            converted_count = converted_result.modified_count

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

            reverted_result = await summary_collection.update_many(
                {"room_id": {"$type": "objectId"}},
                [{"$set": {"room_id": {"$toString": "$room_id"}}}],
            )
            reverted_count = reverted_result.modified_count

            logger.info(f"✅ Reverted {reverted_count} rooms_summary room_id values back to string")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to rollback rooms_summary cleanup: {e}")
            return False