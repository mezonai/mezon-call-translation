"""
PostgreSQL repository for user permissions.
Mirrors UserPermissionService (MongoDB) interface exactly.
In-memory cache preserved for performance.
permissions stored as JSONB array of strings.
"""

from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import User
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgUserPermissionRepository:
    """PostgreSQL-backed user permission store. Drop-in for UserPermissionService."""

    def __init__(self):
        # In-memory cache: user_id -> Set[str]
        self._cache: dict[str, set[str]] = {}

    # ------------------------------------------------------------------
    # Permission queries
    # ------------------------------------------------------------------

    async def get_user_permissions(self, user_id: str) -> set[str]:
        """Return flat permission set for a user (uses cache)."""
        if user_id in self._cache:
            return self._cache[user_id]

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                user = await session.get(User, user_id)
                if not user:
                    logger.debug(f"No user found for user_id={user_id}")
                    return set()
                permissions_list = user.permissions or []
                permissions = set(permissions_list)
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
        display_name: str | None = None,
        permissions: list[str] | None = None,
        avatar_url: str | None = None,
    ) -> bool:
        """Upsert a user record."""
        now = datetime.now(UTC)
        session_factory = get_session_factory()

        insert_values = {
            "id": user_id,
            "username": username,
            "display_name": display_name,
            "avatar_url": avatar_url,
            "permissions": permissions if permissions is not None else [],
            "created_at": now,
            "updated_at": now,
        }

        try:
            async with session_factory() as session:
                stmt = insert(User).values(**insert_values)
                update_dict = {"username": stmt.excluded.username, "updated_at": stmt.excluded.updated_at}
                if display_name is not None:
                    update_dict["display_name"] = stmt.excluded.display_name
                if avatar_url is not None:
                    update_dict["avatar_url"] = stmt.excluded.avatar_url
                if permissions is not None:
                    update_dict["permissions"] = stmt.excluded.permissions
                stmt = stmt.on_conflict_do_update(index_elements=[User.id], set_=update_dict)
                await session.execute(stmt)
                await session.commit()

            logger.info(f"Created/updated user user_id={user_id}, username={username}")
            self._cache.pop(user_id, None)
            return True
        except Exception as e:
            logger.error(f"Failed to create/update user: {e}")
            return False

    async def get_user_info(self, user_id: str) -> User | None:
        """Return full user document or None."""
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                user_obj = await session.get(User, user_id)
                return user_obj
        except Exception as e:
            logger.error(f"Failed to get user info: {e}")
            return None


# --------------- Singleton ---------------
_pg_user_permission_repository: PgUserPermissionRepository | None = None


def get_pg_user_permission_repository() -> PgUserPermissionRepository:
    global _pg_user_permission_repository
    if _pg_user_permission_repository is None:
        _pg_user_permission_repository = PgUserPermissionRepository()
    return _pg_user_permission_repository
