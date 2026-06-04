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

        insert_params = {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "avatar_url": avatar_url,
            "permissions": json.dumps(permissions if permissions is not None else []),
            "now": now,
        }

        update_clauses = ["username = EXCLUDED.username", "updated_at = EXCLUDED.updated_at"]

        if display_name is not None:
            update_clauses.append("display_name = EXCLUDED.display_name")

        if avatar_url is not None:
            update_clauses.append("avatar_url = EXCLUDED.avatar_url")

        if permissions is not None:
            update_clauses.append("permissions = EXCLUDED.permissions")

        update_sql = ", ".join(update_clauses)

        query = f"""
            INSERT INTO users (id, username, display_name, avatar_url, permissions, created_at, updated_at)
            VALUES (:id, :username, :display_name, :avatar_url, CAST(:permissions AS jsonb), :now, :now)
            ON CONFLICT (id) DO UPDATE 
            SET {update_sql}
        """
        try:
            async with session_factory() as session:
                await session.execute(text(query), insert_params)
                await session.commit()
                
            logger.info(f"Created/updated user user_id={user_id}, username={username}")
            self._cache.pop(user_id, None)
            return True
        except Exception as e:
            logger.error(f"Failed to create/update user: {e}")
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
                return doc
            return None
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return None