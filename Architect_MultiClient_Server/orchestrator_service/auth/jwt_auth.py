"""
JWT Authentication Middleware for Mezon OAuth2

Validates JWT tokens issued by the orchestrator service after successful
Mezon OAuth2 authentication. Used to protect endpoints requiring user authentication.

Features:
- Verify JWT signature and expiration
- Check if token is blacklisted (revoked)
- Extract user information from token claims
"""

from typing import Optional, Dict, Any
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
import jwt

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.jwt_utils import verify_jwt_token
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.services.mongodb.token_blacklist_service import TokenBlacklistService

logger = get_logger(__name__)

# Security scheme for JWT Bearer tokens
security = HTTPBearer(auto_error=False)


async def verify_jwt(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Dict[str, Any]:
    """
    Verify JWT token issued by orchestrator after Mezon OAuth2 authentication.

    Performs the following checks:
    1. Token signature and expiration
    2. Token blacklist status (logout/revoked)

    Args:
        credentials: HTTP Authorization credentials from Bearer token

    Returns:
        Dict containing user information from JWT claims:
        - jti: JWT ID (unique identifier)
        - user_id: User ID from Mezon
        - username: Username
        - display_name: Display name
        - avatar_url: Avatar URL
        - exp: Expiration timestamp
        - iat: Issued at timestamp

    Raises:
        HTTPException: 401 if authentication fails, missing, token expired, or blacklisted
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

        # Extract JTI for blacklist check
        jti = payload.get("jti")

        if not jti:
            logger.warning("JWT token missing JTI claim")
            raise HTTPException(
                status_code=401,
                detail="Invalid token format.",
                headers={"WWW-Authenticate": "Bearer"}
            )

        # Check if token is blacklisted
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()

        blacklist_service = TokenBlacklistService(mongodb.db)
        is_blacklisted = await blacklist_service.is_blacklisted(jti)

        if is_blacklisted:
            logger.warning(f"Blacklisted token used: jti={jti}, user_id={payload.get('user_id')}")
            raise HTTPException(
                status_code=401,
                detail="Token has been revoked. Please login again.",
                headers={"WWW-Authenticate": "Bearer"}
            )

        logger.debug(f"JWT authentication successful for user_id={payload.get('user_id')}, jti={jti}")

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
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error during JWT verification: {e}")
        raise HTTPException(
            status_code=500,
            detail="Authentication verification failed."
        )
