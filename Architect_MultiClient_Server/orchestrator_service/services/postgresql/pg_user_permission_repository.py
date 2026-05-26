"""
PostgreSQL repository for user permissions.
Mirrors UserPermissionService (MongoDB) interface exactly.
In-memory cache preserved for performance.
permissions stored as JSONB array of strings.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Set

from sqlalchemy import text

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgUserPermissionRepository:
    """PostgreSQL-backed user permission store. Drop-in for UserPermissionService."""

    def __init__(self):
        # In-memory cache: user_id -> Set[str]
        self._cache: Dict[str, Set[str]] = {}

    # ------------------------------------------------------------------
    # Permission queries
    # ------------------------------------------------------------------

    async def get_user_permissions(self, user_id: str) -> Set[str]:
        """Return flat permission set for a user (uses cache)."""
        if user_id in self._cache:
            return self._cache[user_id]

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("SELECT permissions FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                row = result.fetchone()
            if row and row[0]:
                permissions = set(row[0])
                self._cache[user_id] = permissions
                logger.debug(f"Loaded {len(permissions)} permissions for user_id={user_id}")
                return permissions
            logger.debug(f"No user found for user_id={user_id}")
            return set()
        except Exception as e:
            logger.error(f"Failed to get user permissions: {e}")
            return set()

    # ------------------------------------------------------------------
    # User CRUD
    # ------------------------------------------------------------------

    async def create_or_update_user(
        self,
        user_id: str,
        username: str,
        display_name: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        avatar_url: Optional[str] = None,
    ) -> bool:
        """Upsert a user record."""
        now = datetime.now(timezone.utc)
        import json

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                # Check if user exists
                result = await session.execute(
                    text("SELECT id FROM users WHERE id = :id"),
                    {"id": user_id},
                )
                exists = result.fetchone() is not None

                if exists:
                    # Build dynamic SET clause
                    updates = {"username": username, "updated_at": now}
                    if display_name is not None:
                        updates["display_name"] = display_name
                    if avatar_url is not None:
                        updates["avatar_url"] = avatar_url
                    if permissions is not None:
                        updates["permissions"] = json.dumps(permissions)

                    set_parts = ", ".join(
                        f"{k} = :{k}" for k in updates if k != "permissions"
                    )
                    params = {k: v for k, v in updates.items() if k != "permissions"}
                    params["id"] = user_id
                    if permissions is not None:
                        set_parts += ", permissions = :permissions::jsonb"
                        params["permissions"] = json.dumps(permissions)

                    await session.execute(
                        text(f"UPDATE users SET {set_parts} WHERE id = :id"),
                        params,
                    )
                else:
                    perms_json = json.dumps(permissions if permissions is not None else [])
                    await session.execute(
                        text("""
                            INSERT INTO users
                                (id, username, display_name, avatar_url, permissions,
                                created_at, updated_at)
                            VALUES
                                (:id, :username, :display_name, :avatar_url,
                                CAST(:permissions AS jsonb), :now, :now)
                        """),
                        {
                            "id": user_id,
                            "username": username,
                            "display_name": display_name,
                            "avatar_url": avatar_url,
                            "permissions": perms_json,
                            "now": now,
                        },
                    )

                await session.commit()
            logger.info(f"Created/updated user user_id={user_id}, username={username}")
            self._cache.pop(user_id, None)
            return True
        except Exception as e:
            logger.error(f"Failed to create/update user: {e}")
            return False

    async def grant_permission(self, user_id: str, permission: str) -> bool:
        """Add a single permission to user (idempotent)."""
        import json
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                        UPDATE users
                        SET permissions = (
                            CASE
                                WHEN permissions @> :perm::jsonb THEN permissions
                                ELSE permissions || :perm::jsonb
                            END
                        ),
                        updated_at = :now
                        WHERE id = :id
                    """),
                    {
                        "perm": json.dumps([permission]),
                        "now": datetime.now(timezone.utc),
                        "id": user_id,
                    },
                )
                await session.commit()
            self._cache.pop(user_id, None)
            logger.info(f"Granted permission '{permission}' to user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to grant permission: {e}")
            return False

    async def revoke_permission(self, user_id: str, permission: str) -> bool:
        """Remove a single permission from user."""
        import json
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                # Remove element matching the permission string from JSONB array
                await session.execute(
                    text("""
                        UPDATE users
                        SET permissions = (
                            SELECT jsonb_agg(elem)
                            FROM jsonb_array_elements(permissions) AS elem
                            WHERE elem::text != :perm_quoted
                        ),
                        updated_at = :now
                        WHERE id = :id
                    """),
                    {
                        "perm_quoted": json.dumps(permission),
                        "now": datetime.now(timezone.utc),
                        "id": user_id,
                    },
                )
                await session.commit()
            self._cache.pop(user_id, None)
            logger.info(f"Revoked permission '{permission}' from user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to revoke permission: {e}")
            return False

    async def grant_permissions_bulk(self, user_id: str, permissions: List[str]) -> bool:
        """Add multiple permissions (idempotent, no duplicates)."""
        import json
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(
                    text("""
                        UPDATE users
                        SET permissions = (
                            SELECT jsonb_agg(DISTINCT elem)
                            FROM (
                                SELECT jsonb_array_elements(permissions) AS elem
                                UNION ALL
                                SELECT jsonb_array_elements(:new_perms::jsonb) AS elem
                            ) combined
                        ),
                        updated_at = :now
                        WHERE id = :id
                    """),
                    {
                        "new_perms": json.dumps(permissions),
                        "now": datetime.now(timezone.utc),
                        "id": user_id,
                    },
                )
                await session.commit()
            self._cache.pop(user_id, None)
            logger.info(f"Granted {len(permissions)} permissions to user_id={user_id}")
            return True
        except Exception as e:
            logger.error(f"Failed to grant permissions in bulk: {e}")
            return False

    async def set_permissions(self, user_id: str, permissions: List[str]) -> bool:
        """Replace all permissions for a user."""
        import json
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("""
                        UPDATE users
                        SET permissions = :perms::jsonb,
                            updated_at  = :now
                        WHERE id = :id
                        RETURNING id
                    """),
                    {
                        "perms": json.dumps(permissions),
                        "now": datetime.now(timezone.utc),
                        "id": user_id,
                    },
                )
                row = result.fetchone()
                await session.commit()
            if row:
                self._cache.pop(user_id, None)
                logger.info(f"Set {len(permissions)} permissions for user_id={user_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to set permissions: {e}")
            return False

    async def get_user_info(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return full user document or None."""
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("""
                        SELECT id, username, display_name, avatar_url,
                               permissions, created_at, updated_at
                        FROM users WHERE id = :id
                    """),
                    {"id": user_id},
                )
                row = result.fetchone()
            if row:
                doc = dict(row._mapping)
                doc["user_id"] = doc["id"]   # backward compat
                doc["_id"] = doc["id"]
                return doc
            return None
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return None

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user and clear their cache."""
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("DELETE FROM users WHERE id = :id RETURNING id"),
                    {"id": user_id},
                )
                row = result.fetchone()
                await session.commit()
            if row:
                self._cache.pop(user_id, None)
                logger.info(f"Deleted user user_id={user_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Failed to delete user: {e}")
            return False

    async def list_users_with_permission(self, permission: str) -> List[Dict[str, Any]]:
        """List all users who have a specific permission."""
        import json
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("""
                        SELECT id, username, display_name, avatar_url,
                               permissions, created_at, updated_at
                        FROM users
                        WHERE permissions @> :perm::jsonb
                        LIMIT 1000
                    """),
                    {"perm": json.dumps([permission])},
                )
                rows = result.fetchall()
            users = []
            for row in rows:
                doc = dict(row._mapping)
                doc["user_id"] = doc["id"]
                doc["_id"] = doc["id"]
                users.append(doc)
            logger.debug(f"Found {len(users)} users with permission '{permission}'")
            return users
        except Exception as e:
            logger.error(f"Failed to list users with permission: {e}")
            return []

    async def clear_cache(self, user_id: Optional[str] = None):
        """Clear in-memory permission cache."""
        if user_id:
            self._cache.pop(user_id, None)
            logger.debug(f"Cleared cache for user_id={user_id}")
        else:
            self._cache.clear()
            logger.debug("Cleared all user permission cache")
