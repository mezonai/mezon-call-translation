"""
User Permission Service (Flat Permission Model)

Manages user permissions with flat permission list approach:
- Each user has direct list of permissions (no role mapping)
- Grant/revoke permissions individually or in bulk
- Cache user permissions for performance

"""

from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class UserPermissionService:
    """Service for managing flat user permissions in MongoDB"""

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize user permission service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.users_collection = db.users
        self.permissions_collection = db.permissions
        # Cache for user permissions (user_id -> set of permissions)
        self._cache: Dict[str, Set[str]] = {}

    async def get_user_permissions(self, user_id: str) -> Set[str]:
        """
        Get flat permissions list for a user.
        Uses cache for performance.

        Args:
            user_id: User ID

        Returns:
            Set of permission strings
        """
        # Check cache first
        if user_id in self._cache:
            return self._cache[user_id]

        try:
            user_doc = await self.users_collection.find_one({"_id": user_id})

            if user_doc:
                permissions = set(user_doc.get("permissions", []))
                self._cache[user_id] = permissions
                logger.debug(f"Loaded {len(permissions)} permissions for user_id={user_id}")
                return permissions
            else:
                logger.debug(f"No user found for user_id={user_id}")
                return set()

        except Exception as e:
            logger.error(f"Failed to get user permissions: {e}")
            return set()

    async def create_or_update_user(
        self,
        user_id: str,
        username: str,
        display_name: Optional[str] = None,
        permissions: Optional[List[str]] = None
    ) -> bool:
        """
        Create or update user with permissions.

        Args:
            user_id: User ID
            username: Username
            display_name: Display name
            permissions: List of permission strings (if None, keeps existing or initializes empty)

        Returns:
            True if successful
        """
        now = datetime.now(timezone.utc)

        try:
            # Build $set document - fields to update for both new and existing
            set_doc = {
                "username": username,
                "updated_at": now
            }

            # Only add display_name if provided
            if display_name is not None:
                set_doc["display_name"] = display_name

            # Only add permissions to $set if explicitly provided
            if permissions is not None:
                set_doc["permissions"] = permissions

            # Build $setOnInsert - fields to set ONLY for new documents
            set_on_insert_doc = {
                "created_at": now
            }

            # Only add permissions to $setOnInsert if NOT already in $set
            # This avoids MongoDB conflict of same field in both operators
            if permissions is None:
                set_on_insert_doc["permissions"] = []

            result = await self.users_collection.update_one(
                {"_id": user_id},
                {
                    "$set": set_doc,
                    "$setOnInsert": set_on_insert_doc
                },
                upsert=True
            )

            if result.upserted_id or result.modified_count > 0:
                logger.info(f"Created/updated user user_id={user_id}, username={username}")
                # Clear cache
                self._cache.pop(user_id, None)
                return True

            return True

        except Exception as e:
            logger.error(f"Failed to create/update user: {e}")
            return False

    async def grant_permission(self, user_id: str, permission: str) -> bool:
        """
        Grant a single permission to user.

        Args:
            user_id: User ID
            permission: Permission string to grant

        Returns:
            True if successful
        """
        try:
            result = await self.users_collection.update_one(
                {"_id": user_id},
                {
                    "$addToSet": {"permissions": permission},
                    "$set": {"updated_at": datetime.now(timezone.utc)}
                }
            )

            if result.modified_count > 0:
                logger.info(f"Granted permission '{permission}' to user_id={user_id}")
                self._cache.pop(user_id, None)
                return True
            else:
                logger.debug(f"Permission '{permission}' already exists for user_id={user_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to grant permission: {e}")
            return False

    async def revoke_permission(self, user_id: str, permission: str) -> bool:
        """
        Revoke a single permission from user.

        Args:
            user_id: User ID
            permission: Permission string to revoke

        Returns:
            True if successful
        """
        try:
            result = await self.users_collection.update_one(
                {"_id": user_id},
                {
                    "$pull": {"permissions": permission},
                    "$set": {"updated_at": datetime.now(timezone.utc)}
                }
            )

            if result.modified_count > 0:
                logger.info(f"Revoked permission '{permission}' from user_id={user_id}")
                self._cache.pop(user_id, None)
                return True
            else:
                logger.debug(f"Permission '{permission}' not found for user_id={user_id}")
                return True

        except Exception as e:
            logger.error(f"Failed to revoke permission: {e}")
            return False

    async def grant_permissions_bulk(self, user_id: str, permissions: List[str]) -> bool:
        """
        Grant multiple permissions to user at once.

        Args:
            user_id: User ID
            permissions: List of permission strings

        Returns:
            True if successful
        """
        try:
            result = await self.users_collection.update_one(
                {"_id": user_id},
                {
                    "$addToSet": {"permissions": {"$each": permissions}},
                    "$set": {"updated_at": datetime.now(timezone.utc)}
                }
            )

            if result.modified_count > 0:
                logger.info(f"Granted {len(permissions)} permissions to user_id={user_id}")
                self._cache.pop(user_id, None)
                return True

            return True

        except Exception as e:
            logger.error(f"Failed to grant permissions in bulk: {e}")
            return False

    async def set_permissions(self, user_id: str, permissions: List[str]) -> bool:
        """
        Set user permissions (replace all existing permissions).

        Args:
            user_id: User ID
            permissions: Complete list of permission strings

        Returns:
            True if successful
        """
        try:
            result = await self.users_collection.update_one(
                {"_id": user_id},
                {
                    "$set": {
                        "permissions": permissions,
                        "updated_at": datetime.now(timezone.utc)
                    }
                }
            )

            if result.modified_count > 0:
                logger.info(f"Set {len(permissions)} permissions for user_id={user_id}")
                self._cache.pop(user_id, None)
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to set permissions: {e}")
            return False

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Get full user information including permissions.

        Args:
            user_id: User ID

        Returns:
            User document or None
        """
        try:
            user_doc = await self.users_collection.find_one({"_id": user_id})

            if user_doc:
                user_doc["user_id"] = user_doc["_id"]
                return user_doc

            return None

        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return None

    async def delete_user(self, user_id: str) -> bool:
        """
        Delete user and all permissions.

        Args:
            user_id: User ID

        Returns:
            True if deleted
        """
        try:
            result = await self.users_collection.delete_one({"_id": user_id})

            if result.deleted_count > 0:
                logger.info(f"Deleted user user_id={user_id}")
                self._cache.pop(user_id, None)
                return True

            return False

        except Exception as e:
            logger.error(f"Failed to delete user: {e}")
            return False

    async def list_users_with_permission(self, permission: str) -> List[Dict[str, Any]]:
        """
        List all users who have a specific permission.

        Args:
            permission: Permission string

        Returns:
            List of user documents
        """
        try:
            cursor = self.users_collection.find({"permissions": permission})
            users = await cursor.to_list(length=1000)

            for user in users:
                user["user_id"] = user["_id"]

            logger.debug(f"Found {len(users)} users with permission '{permission}'")
            return users

        except Exception as e:
            logger.error(f"Failed to list users with permission: {e}")
            return []

    async def clear_cache(self, user_id: Optional[str] = None):
        """
        Clear permission cache.

        Args:
            user_id: If provided, clear only this user's cache. Otherwise clear all.
        """
        if user_id:
            self._cache.pop(user_id, None)
            logger.debug(f"Cleared cache for user_id={user_id}")
        else:
            self._cache.clear()
            logger.debug("Cleared all user permission cache")


class PermissionDefinitionService:
    """Service for managing permission definitions (metadata)"""

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize permission definition service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.collection = db.permissions

    async def create_permission(
        self,
        permission: str,
        resource: str,
        action: str,
        description: str
    ) -> bool:
        """
        Create a permission definition.

        Args:
            permission: Full permission string (e.g., "rooms:view_all")
            resource: Resource name (e.g., "rooms")
            action: Action name (e.g., "view_all")
            description: Human-readable description

        Returns:
            True if successful
        """
        now = datetime.now(timezone.utc)

        try:
            await self.collection.update_one(
                {"_id": permission},
                {
                    "$set": {
                        "resource": resource,
                        "action": action,
                        "description": description,
                        "updated_at": now
                    },
                    "$setOnInsert": {
                        "created_at": now
                    }
                },
                upsert=True
            )

            logger.info(f"Created/updated permission definition: {permission}")
            return True

        except Exception as e:
            logger.error(f"Failed to create permission: {e}")
            return False

    async def get_all_permissions(self) -> List[Dict[str, Any]]:
        """
        Get all permission definitions.

        Returns:
            List of permission documents
        """
        try:
            cursor = self.collection.find({})
            permissions = await cursor.to_list(length=1000)

            for perm in permissions:
                perm["permission"] = perm["_id"]

            return permissions

        except Exception as e:
            logger.error(f"Failed to get all permissions: {e}")
            return []

    async def get_permissions_by_resource(self, resource: str) -> List[Dict[str, Any]]:
        """
        Get all permissions for a specific resource.

        Args:
            resource: Resource name (e.g., "rooms")

        Returns:
            List of permission documents
        """
        try:
            cursor = self.collection.find({"resource": resource})
            permissions = await cursor.to_list(length=100)

            for perm in permissions:
                perm["permission"] = perm["_id"]

            return permissions

        except Exception as e:
            logger.error(f"Failed to get permissions by resource: {e}")
            return []
