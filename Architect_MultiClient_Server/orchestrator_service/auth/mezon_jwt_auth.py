"""
JWT Authentication Middleware for Mezon OAuth2

Validates JWT tokens issued by the orchestrator service after successful
Mezon OAuth2 authentication. Used to protect endpoints requiring user authentication.

Usage:
    from orchestrator_service.auth.mezon_jwt_auth import verify_mezon_jwt

    @router.get("/endpoint")
    async def endpoint(user: dict = Depends(verify_mezon_jwt)):
        # user contains: user_id, username, display_name, avatar_url
        pass
"""

from typing import Optional, Dict, Any
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.jwt_utils import verify_jwt_token

logger = get_logger(__name__)

# Security scheme for JWT Bearer tokens
security = HTTPBearer(auto_error=False)


async def verify_mezon_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Dict[str, Any]:
    """
    Verify JWT token issued by orchestrator after Mezon OAuth2 authentication.

    Args:
        credentials: HTTP Authorization credentials from Bearer token

    Returns:
        Dict containing user information from JWT claims:
        - user_id: User ID from Mezon
        - username: Username
        - display_name: Display name
        - avatar_url: Avatar URL
        - exp: Expiration timestamp
        - iat: Issued at timestamp

    Raises:
        HTTPException: 401 if authentication fails, missing, or token expired
    """
    # Check if credentials provided
    if not credentials:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header. Please login with Mezon.",
            headers={"WWW-Authenticate": "Bearer"}
        )

    token = credentials.credentials

    try:
        # Verify and decode JWT token
        payload = verify_jwt_token(token)

        logger.debug(f"JWT authentication successful for user_id={payload.get('user_id')}")

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        raise HTTPException(
            status_code=401,
            detail="Token has expired. Please login again.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        raise HTTPException(
            status_code=401,
            detail="Invalid authentication token.",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except Exception as e:
        logger.error(f"Unexpected error during JWT verification: {e}")
        raise HTTPException(
            status_code=500,
            detail="Authentication verification failed."
        )
