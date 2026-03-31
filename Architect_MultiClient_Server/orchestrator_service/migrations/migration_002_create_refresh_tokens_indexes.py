"""
Migration: Create indexes for refresh_tokens collection

This migration creates:
- TTL index on expires_at field (auto-delete expired tokens)
- Index on user_id for querying user's tokens
- Index on access_token_jti for quick lookup
"""
from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CreateRefreshTokensIndexes(MigrationBase):
    """Create indexes for refresh_tokens collection with TTL"""

    @property
    def migration_id(self) -> str:
        return "002_create_refresh_tokens_indexes"

    @property
    def description(self) -> str:
        return "Create TTL and query indexes for refresh_tokens collection"

    async def up(self) -> bool:
        """Create indexes for refresh_tokens collection"""
        try:
            tokens_collection = self.db["refresh_tokens"]

            # 1. TTL index - automatically delete expired tokens
            await tokens_collection.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="ttl_expires_at"
            )
            logger.info("✅ Created TTL index on refresh_tokens.expires_at")

            # 2. Index on user_id for querying user's tokens
            await tokens_collection.create_index(
                "user_id",
                name="idx_user_id"
            )
            logger.info("✅ Created index on refresh_tokens.user_id")

            # 3. Index on access_token_jti for quick lookup
            await tokens_collection.create_index(
                "access_token_jti",
                name="idx_access_token_jti"
            )
            logger.info("✅ Created index on refresh_tokens.access_token_jti")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to create refresh_tokens indexes: {e}")
            return False

    async def down(self) -> bool:
        """Drop indexes for refresh_tokens collection"""
        try:
            tokens_collection = self.db["refresh_tokens"]

            # Drop indexes by name
            index_names = [
                "ttl_expires_at",
                "idx_user_id",
                "idx_access_token_jti"
            ]

            for index_name in index_names:
                try:
                    await tokens_collection.drop_index(index_name)
                    logger.info(f"✅ Dropped index: {index_name}")
                except Exception as e:
                    logger.warning(f"⚠️ Could not drop index {index_name}: {e}")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to drop refresh_tokens indexes: {e}")
            return False
