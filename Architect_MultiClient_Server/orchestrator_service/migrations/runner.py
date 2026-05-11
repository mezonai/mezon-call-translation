"""
Migration Runner for MongoDB schema changes

Usage:
    python -m orchestrator_service.migrations.runner up       # Run all pending migrations
    python -m orchestrator_service.migrations.runner down     # Rollback last migration
    python -m orchestrator_service.migrations.runner status   # Show migration status
"""
import asyncio
import sys
from typing import List, Type
from motor.motor_asyncio import AsyncIOMotorClient

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.migrations.migration_base import MigrationBase

# Import all migrations here
from orchestrator_service.migrations.migration_001_create_metadata_events_indexes import CreateMetadataEventsIndexes
from orchestrator_service.migrations.migration_002_create_refresh_tokens_indexes import CreateRefreshTokensIndexes
from orchestrator_service.migrations.migration_003_create_token_blacklist_indexes import CreateTokenBlacklistIndexes
from orchestrator_service.migrations.migration_004_create_flat_permission_model import CreateFlatPermissionModel
from orchestrator_service.migrations.migration_005_add_participants_to_rooms import AddParticipantsToRooms
from .migration_006_cleanup_rooms_summary import CleanupRoomsSummary
from orchestrator_service.migrations.migration_007_replace_minio_localhost_urls import ReplaceMinioLocalhostURLs

logger = get_logger(__name__)


class MigrationRunner:
    """Runs database migrations in order"""

    # Register all migrations here in order
    MIGRATIONS: List[Type[MigrationBase]] = [
        CreateMetadataEventsIndexes,           # 001
        CreateRefreshTokensIndexes,            # 002
        CreateTokenBlacklistIndexes,           # 003
        CreateFlatPermissionModel,             # 004
        AddParticipantsToRooms,                # 005
        CleanupRoomsSummary,                   # 006
        ReplaceMinioLocalhostURLs,             # 007
    ]

    def __init__(self):
        self.config = get_config()
        self.mongo_uri = self._build_mongo_uri()
        self.database_name = self.config.mongodb.database
        self.client = None
        self.db = None

    def _build_mongo_uri(self) -> str:
        """Build MongoDB connection URI with authentication"""
        mongo_config = self.config.mongodb
        return (
            f"mongodb://{mongo_config.username}:{mongo_config.password}@"
            f"{mongo_config.host}:{mongo_config.port}/?authSource=admin"
        )

    async def connect(self):
        """Connect to MongoDB"""
        try:
            self.client = AsyncIOMotorClient(
                self.mongo_uri,
                serverSelectionTimeoutMS=5000,
                connectTimeoutMS=10000,
            )
            self.db = self.client[self.database_name]
            await self.client.admin.command("ping")
            logger.info(f"✅ Connected to MongoDB: {self.database_name}")
        except Exception as e:
            logger.error(f"❌ MongoDB connection failed: {e}")
            raise

    async def disconnect(self):
        """Disconnect from MongoDB"""
        if self.client:
            self.client.close()
            logger.info("✅ MongoDB disconnected")

    async def migrate_up(self):
        """Run all pending migrations"""
        logger.info("🚀 Starting migration: UP")

        applied_count = 0
        for migration_class in self.MIGRATIONS:
            migration = migration_class(self.db)

            # Check if already applied
            if await migration.is_applied():
                logger.info(f"⏭️  Skipping (already applied): {migration.migration_id}")
                continue

            # Run migration
            logger.info(f"📦 Applying migration: {migration.migration_id}")
            logger.info(f"   Description: {migration.description}")

            success = await migration.up()
            if success:
                await migration.mark_applied()
                applied_count += 1
                logger.info(f"✅ Migration applied: {migration.migration_id}")
            else:
                logger.error(f"❌ Migration failed: {migration.migration_id}")
                logger.error("⚠️  Stopping migration process")
                return False

        if applied_count == 0:
            logger.info("✅ All migrations are up to date")
        else:
            logger.info(f"✅ Successfully applied {applied_count} migration(s)")

        return True

    async def migrate_down(self):
        """Rollback the last applied migration"""
        logger.info("🔄 Starting migration: DOWN")

        # Find last applied migration
        migrations_collection = self.db["migrations"]
        last_applied = await migrations_collection.find_one(
            {},
            sort=[("applied_at", -1)]
        )

        if not last_applied:
            logger.info("ℹ️  No migrations to rollback")
            return True

        migration_id = last_applied["migration_id"]
        logger.info(f"📦 Rolling back migration: {migration_id}")

        # Find and run migration down
        for migration_class in self.MIGRATIONS:
            migration = migration_class(self.db)
            if migration.migration_id == migration_id:
                success = await migration.down()
                if success:
                    await migration.mark_reverted()
                    logger.info(f"✅ Migration rolled back: {migration_id}")
                    return True
                else:
                    logger.error(f"❌ Rollback failed: {migration_id}")
                    return False

        logger.error(f"❌ Migration not found: {migration_id}")
        return False

    async def show_status(self):
        """Show status of all migrations"""
        logger.info("📋 Migration Status:")
        logger.info("=" * 80)

        for migration_class in self.MIGRATIONS:
            migration = migration_class(self.db)
            is_applied = await migration.is_applied()
            status = "✅ APPLIED" if is_applied else "⏳ PENDING"

            logger.info(f"{status:12} | {migration.migration_id}")
            logger.info(f"{'':12} | {migration.description}")
            logger.info("-" * 80)


async def main():
    """Main entry point for migration runner"""
    if len(sys.argv) < 2:
        logger.info("Usage: python -m orchestrator_service.migrations.runner <command>")
        logger.info("Commands:")
        logger.info("  up      - Run all pending migrations")
        logger.info("  down    - Rollback last migration")
        logger.info("  status  - Show migration status")
        sys.exit(1)

    command = sys.argv[1].lower()

    runner = MigrationRunner()

    try:
        await runner.connect()

        if command == "up":
            success = await runner.migrate_up()
            sys.exit(0 if success else 1)

        elif command == "down":
            success = await runner.migrate_down()
            sys.exit(0 if success else 1)

        elif command == "status":
            await runner.show_status()
            sys.exit(0)

        else:
            logger.error(f"❌ Unknown command: {command}")
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Migration failed: {e}")
        sys.exit(1)

    finally:
        await runner.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
