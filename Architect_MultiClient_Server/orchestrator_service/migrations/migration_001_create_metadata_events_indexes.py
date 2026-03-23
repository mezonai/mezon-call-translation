"""
Migration: Create indexes for metadata_events collection

This migration creates:
- TTL index on created_at field (3 days expiration)
- Compound index on (room_id, created_at) for room-based queries
- Compound index on (event_type, created_at) for event type queries
- Index on event_id for UUID lookups
"""
from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CreateMetadataEventsIndexes(MigrationBase):
    """Create indexes for metadata_events collection with TTL"""

    @property
    def migration_id(self) -> str:
        return "001_create_metadata_events_indexes"

    @property
    def description(self) -> str:
        return "Create TTL and query indexes for metadata_events collection"

    async def up(self) -> bool:
        """Create indexes for metadata_events collection"""
        try:
            events_collection = self.db["metadata_events"]
            # 1. TTL index - automatically delete events after 3 days
            await events_collection.create_index(
                "created_at",
                expireAfterSeconds=259200,  # 3 days in seconds
                name="ttl_created_at"
            )
            logger.info("✅ Created TTL index on metadata_events.created_at (3 days)")

            # 2. Compound index for room_id queries with time sorting
            await events_collection.create_index(
                [("room_id", 1), ("created_at", -1)],
                name="idx_room_id_created_at"
            )
            logger.info("✅ Created compound index on metadata_events (room_id, created_at)")

            # 3. Compound index for event_type queries with time sorting
            await events_collection.create_index(
                [("event_type", 1), ("created_at", -1)],
                name="idx_event_type_created_at"
            )
            logger.info("✅ Created compound index on metadata_events (event_type, created_at)")

            # 4. Index for event_id lookup (UUID)
            await events_collection.create_index(
                "event_id",
                name="idx_event_id"
            )
            logger.info("✅ Created index on metadata_events.event_id")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to create metadata_events indexes: {e}")
            return False

    async def down(self) -> bool:
        """Drop indexes for metadata_events collection"""
        try:
            events_collection = self.db["metadata_events"]

            # Drop indexes by name
            index_names = [
                "ttl_created_at",
                "idx_room_id_created_at",
                "idx_event_type_created_at",
                "idx_event_id"
            ]

            for index_name in index_names:
                try:
                    await events_collection.drop_index(index_name)
                    logger.info(f"✅ Dropped index: {index_name}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not drop index {index_name}: {e}")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to drop metadata_events indexes: {e}")
            return False
