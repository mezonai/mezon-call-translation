"""
Permission-Based Authorization System (Flat Permission Model)

Features:
- Flat permission model: users have direct permission lists
- Load permissions from MongoDB users collection
- Fallback to empty permissions if DB unavailable
- FastAPI dependencies for authorization
- Easy to grant/revoke permissions per user
"""

from typing import Dict, Any, Set, Optional
from fastapi import Depends, HTTPException
from orchestrator_service.auth.jwt_auth import verify_jwt
from orchestrator_service.services.mongodb.user_permission_service import UserPermissionService

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

# Global UserPermissionService instance (initialized on startup)
_user_permission_service: Optional[UserPermissionService] = None


def set_user_permission_service(service: UserPermissionService):
    """
    Set the global user permission service instance.
    Should be called during application startup.

    Args:
        service: UserPermissionService instance
    """
    global _user_permission_service
    _user_permission_service = service
    logger.info("User permission service initialized for authorization")


def get_user_permission_service() -> Optional[UserPermissionService]:
    """
    Get the global user permission service instance.

    Returns:
        UserPermissionService instance or None if not initialized
    """
    return _user_permission_service


class AuthContext:
    """
    Authorization context with permission checking.
    Extracted from JWT token - permissions loaded from users collection.
    """

    def __init__(self, user: Dict[str, Any], permissions: Set[str]):
        """
        Initialize authorization context.

        Args:
            user: User dict from JWT token (contains user_id, username, etc.)
            permissions: Set of flat permissions for this user
        """
        self.user = user
        self.user_id = user["user_id"]
        self.username = user.get("username", "")
        self.display_name = user.get("display_name", "")
        self.permissions = permissions

        if not self.permissions:
            logger.warning(f"No permissions found for user_id={self.user_id}")

    def has_permission(self, permission: str) -> bool:
        """
        Check if user has specific permission.

        Args:
            permission: Permission string (e.g., "rooms:view_all")

        Returns:
            True if user has permission, False otherwise
        """
        return permission in self.permissions

    def require_permission(self, permission: str):
        """
        Require specific permission, raise HTTPException if not authorized.

        Args:
            permission: Required permission

        Raises:
            HTTPException: 403 if user doesn't have permission
        """
        if not self.has_permission(permission):
            logger.warning(
                f"Permission denied: user_id={self.user_id} (username={self.username}) "
                f"requires '{permission}'"
            )
            raise HTTPException(
                status_code=403,
                detail=f"Permission required: {permission}"
            )

    def has_any_permission(self, *permissions: str) -> bool:
        """
        Check if user has any of the given permissions.

        Args:
            permissions: Variable number of permission strings

        Returns:
            True if user has at least one permission
        """
        return any(p in self.permissions for p in permissions)

    def has_all_permissions(self, *permissions: str) -> bool:
        """
        Check if user has all of the given permissions.

        Args:
            permissions: Variable number of permission strings

        Returns:
            True if user has all permissions
        """
        return all(p in self.permissions for p in permissions)

    # Convenience properties for common checks
    @property
    def can_view_all_rooms(self) -> bool:
        """Can view all rooms"""
        return self.has_permission("rooms:view_all")

    def __repr__(self) -> str:
        return f"AuthContext(user_id={self.user_id}, username={self.username}, permissions={len(self.permissions)})"


async def get_auth_context(
    user: Dict[str, Any] = Depends(verify_jwt)
) -> AuthContext:
    """
    Extract authorization context from JWT token.
    Loads flat permissions from users collection in database.

    Args:
        user: User dict from verify_jwt dependency

    Returns:
        AuthContext with permissions loaded from database
    """
    user_id = user.get("user_id")

    # Try to load permissions from database
    permissions: Set[str] = set()
    permission_service = get_user_permission_service()

    if permission_service and user_id:
        try:
            # Load flat permissions from users collection
            permissions = await permission_service.get_user_permissions(user_id)
            if permissions:
                logger.debug(f"Loaded {len(permissions)} permissions from DB for user_id={user_id}")
            else:
                logger.warning(f"No permissions found in DB for user_id={user_id}")
        except Exception as e:
            logger.error(f"Failed to load permissions from DB for user_id={user_id}: {e}")

    return AuthContext(user, permissions)


def require_permission(permission: str):
    """
    Dependency factory: require specific permission.

    Usage:
        @router.delete("/admin/resource")
        async def delete_resource(
            auth: AuthContext = Depends(require_permission("resource:delete_any"))
        ):
            # Only users with "resource:delete_any" permission can access
            ...

    Args:
        permission: Required permission string

    Returns:
        FastAPI dependency function
    """
    async def check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        auth.require_permission(permission)
        return auth

    return check


def require_any_permission(*permissions: str):
    """
    Dependency factory: require at least one of the given permissions.

    Args:
        permissions: Variable number of permission strings

    Returns:
        FastAPI dependency function
    """
    async def check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_any_permission(*permissions):
            logger.warning(
                f"Permission denied: user_id={auth.user_id} (username={auth.username}) "
                f"requires one of: {', '.join(permissions)}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"One of these permissions required: {', '.join(permissions)}"
            )
        return auth

    return check


def require_all_permissions(*permissions: str):
    """
    Dependency factory: require all of the given permissions.

    Usage:
        @router.delete("/admin/resource")
        async def delete_resource(
            auth: AuthContext = Depends(require_all_permissions("rooms:view_all", "rooms:delete"))
        ):
            # Users must have ALL permissions
            ...

    Args:
        permissions: Variable number of permission strings

    Returns:
        FastAPI dependency function
    """
    async def check(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
        if not auth.has_all_permissions(*permissions):
            logger.warning(
                f"Permission denied: user_id={auth.user_id} (username={auth.username}) "
                f"requires all of: {', '.join(permissions)}"
            )
            raise HTTPException(
                status_code=403,
                detail=f"All of these permissions required: {', '.join(permissions)}"
            )
        return auth

    return check
