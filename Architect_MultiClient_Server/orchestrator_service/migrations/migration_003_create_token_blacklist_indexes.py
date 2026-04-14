"""
Migration: Create indexes for token_blacklist collection

This migration creates:
- TTL index on expires_at field (auto-delete expired blacklist entries)
- Unique index on jti for fast token lookup
- Index on user_id for querying user's blacklisted tokens
"""
from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CreateTokenBlacklistIndexes(MigrationBase):
    """Create indexes for token_blacklist collection with TTL"""

    @property
    def migration_id(self) -> str:
        return "003_create_token_blacklist_indexes"

    @property
    def description(self) -> str:
        return "Create TTL and query indexes for token_blacklist collection"

    async def up(self) -> bool:
        """Create indexes for token_blacklist collection"""
        try:
            blacklist_collection = self.db["token_blacklist"]

            # 1. TTL index - automatically delete expired blacklist entries
            await blacklist_collection.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="ttl_expires_at"
            )
            logger.info("✅ Created TTL index on token_blacklist.expires_at")

            # 2. Unique index on jti for fast lookup
            await blacklist_collection.create_index(
                "jti",
                unique=True,
                name="unique_jti"
            )
            logger.info("✅ Created unique index on token_blacklist.jti")

            # 3. Index on user_id
            await blacklist_collection.create_index(
                "user_id",
                name="idx_user_id"
            )
            logger.info("✅ Created index on token_blacklist.user_id")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to create token_blacklist indexes: {e}")
            return False

    async def down(self) -> bool:
        """Drop indexes for token_blacklist collection"""
        try:
            blacklist_collection = self.db["token_blacklist"]

            # Drop indexes by name
            index_names = [
                "ttl_expires_at",
                "unique_jti",
                "idx_user_id"
            ]

            for index_name in index_names:
                try:
                    await blacklist_collection.drop_index(index_name)
                    logger.info(f"✅ Dropped index: {index_name}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not drop index {index_name}: {e}")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to drop token_blacklist indexes: {e}")
            return False
