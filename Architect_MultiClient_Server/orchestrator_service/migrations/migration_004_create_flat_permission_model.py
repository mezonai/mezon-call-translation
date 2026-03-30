"""
Migration 004: Create Flat Permission Model

This migration creates the complete flat permission system:
1. Creates 'permissions' collection with all permission definitions
2. Creates 'users' collection with flat permission lists
3. Seeds default permissions
4. Creates necessary indexes

This is the foundation for the permission-based authorization system.
users have direct permissions.
"""
from datetime import datetime, timezone
from orchestrator_service.migrations.migration_base import MigrationBase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class CreateFlatPermissionModel(MigrationBase):
    """Create flat permission model (permissions + users collections)"""

    @property
    def migration_id(self) -> str:
        return "004_create_flat_permission_model"

    @property
    def description(self) -> str:
        return "Create flat permission model with permissions and users collections"

    async def up(self) -> bool:
        """Create permissions and users collections with seed data"""
        try:
            collections = await self.db.list_collection_names()
            now = datetime.now(timezone.utc)

            # ===== STEP 1: Create permissions collection =====
            if "permissions" not in collections:
                await self.db.create_collection("permissions")
                logger.info("✅ Created permissions collection")
            else:
                logger.info("ℹ️  permissions collection already exists")

            permissions_collection = self.db["permissions"]

            # Define all permissions with metadata
            all_permissions = [
                # Rooms permissions
                {"_id": "rooms:view_all", "resource": "rooms", "action": "view_all",
                 "description": "View all rooms in the system"},
                {"_id": "rooms:view_own", "resource": "rooms", "action": "view_own",
                 "description": "View only participated rooms"},

                # Queues permissions
                {"_id": "queues:view_stats", "resource": "queues", "action": "view_stats",
                 "description": "View queue statistics"},

                # Metadata events permissions
                {"_id": "metadata_events:view_all", "resource": "metadata_events", "action": "view_all",
                 "description": "View all metadata events"},

                # Chat external permissions
                {"_id": "chat_external:view_all", "resource": "chat_external", "action": "view_all",
                 "description": "View all external chat messages"},

                # Agent permissions
                {"_id": "agent:control", "resource": "agent", "action": "control",
                 "description": "Control agent dispatch and operations"},
            ]

            # Seed permissions
            for perm in all_permissions:
                await permissions_collection.update_one(
                    {"_id": perm["_id"]},
                    {
                        "$set": {
                            "resource": perm["resource"],
                            "action": perm["action"],
                            "description": perm["description"],
                            "updated_at": now
                        },
                        "$setOnInsert": {
                            "created_at": now
                        }
                    },
                    upsert=True
                )

            logger.info(f"✅ Seeded {len(all_permissions)} permission definitions")

            # Create index on resource for efficient queries
            await permissions_collection.create_index("resource", name="idx_resource")
            logger.info("✅ Created index on permissions.resource")

            # ===== STEP 2: Create users collection =====
            if "users" not in collections:
                await self.db.create_collection("users")
                logger.info("✅ Created users collection")
            else:
                logger.info("ℹ️  users collection already exists")

            users_collection = self.db["users"]

            # Create indexes on users collection
            await users_collection.create_index("username", name="idx_username")
            await users_collection.create_index("permissions", name="idx_permissions")
            logger.info("✅ Created indexes on users collection")

            logger.info("🎉 Flat permission model created successfully")
            return True

        except Exception as e:
            logger.error(f"❌ Failed to create flat permission model: {e}")
            return False

    async def down(self) -> bool:
        """Drop permissions and users collections"""
        try:
            await self.db["users"].drop()
            logger.info("✅ Dropped users collection")

            await self.db["permissions"].drop()
            logger.info("✅ Dropped permissions collection")

            return True

        except Exception as e:
            logger.error(f"❌ Failed to drop collections: {e}")
            return False
