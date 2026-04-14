"""
JWT Token Utilities for Mezon OAuth2 Authentication

Provides functions to generate and verify JWT tokens for user sessions.
Tokens contain user information and have configurable expiry times.

Environment Variables:
    JWT_SECRET: Secret key for signing JWT tokens (required)
    JWT_EXPIRY_DAYS: Token expiry in days (default: 1)
"""

import jwt
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, Optional
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)

# Load configuration from centralized config
auth_config = get_config().auth
JWT_SECRET = auth_config.jwt_secret
JWT_EXPIRY_DAYS = auth_config.jwt_expiry_days
JWT_ALGORITHM = "HS256"

# Validate configuration on module load
if not JWT_SECRET:
    logger.warning(
        "⚠️  JWT_SECRET is not set. JWT token generation and validation will fail!"
    )

logger.info(f"JWT Configuration: Algorithm={JWT_ALGORITHM}, Expiry={JWT_EXPIRY_DAYS} days")


def generate_jwt_token(user_data: Dict[str, Any], expiry_days: int = None, jti: Optional[str] = None) -> str:
    """
    Generate a JWT token containing user information.

    Note: Permissions are NOT stored in JWT. They are loaded from database on each request.

    Args:
        user_data: Dictionary containing user information
                  Expected fields: user_id, username, display_name (optional), avatar_url (optional)
        expiry_days: Token expiry in days (defaults to JWT_EXPIRY_DAYS env var)
        jti: Optional JWT ID (unique identifier). If not provided, a new UUID will be generated.

    Returns:
        JWT token string

    Raises:
        ValueError: If JWT_SECRET is not configured or user_data is invalid
    """
    if not JWT_SECRET:
        raise ValueError("JWT_SECRET environment variable is not set")

    if not user_data:
        raise ValueError("user_data cannot be empty")

    # Validate required fields
    if "user_id" not in user_data:
        raise ValueError("user_data must contain 'user_id' field")

    # Use provided expiry or default from env
    expiry = expiry_days if expiry_days is not None else JWT_EXPIRY_DAYS

    # Calculate expiration time
    exp_time = datetime.now(timezone.utc) + timedelta(days=expiry)

    # Generate unique JTI (JWT ID) for token tracking and revocation
    token_jti = jti if jti else str(uuid.uuid4())

    # Build JWT payload (NO role - permissions loaded from DB)
    payload = {
        "jti": token_jti,  # JWT ID for blacklist support
        "user_id": user_data["user_id"],
        "exp": exp_time,
        "iat": datetime.now(timezone.utc),  # Issued at
    }

    # Generate token
    token = jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

    logger.debug(f"Generated JWT token for user_id={user_data['user_id']}, jti={token_jti}, expires={exp_time}")

    return token


def verify_jwt_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        Dictionary containing decoded user claims:
        - jti: JWT ID
        - user_id: User ID
        - username: Username
        - display_name: Display name
        - avatar_url: Avatar URL
        - exp: Expiration timestamp
        - iat: Issued at timestamp

    Raises:
        ValueError: If JWT_SECRET is not configured
        jwt.ExpiredSignatureError: If token has expired
        jwt.InvalidTokenError: If token is invalid or malformed
    """
    if not JWT_SECRET:
        raise ValueError("JWT_SECRET environment variable is not set")

    if not token:
        raise jwt.InvalidTokenError("Token cannot be empty")

    try:
        # Decode and verify token
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])

        logger.debug(f"JWT token verified for user_id={payload.get('user_id')}")

        return payload

    except jwt.ExpiredSignatureError:
        logger.warning("JWT token has expired")
        raise
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid JWT token: {e}")
        raise


def get_token_expiry(token: str) -> datetime:
    """
    Get the expiration time of a JWT token without full verification.

    Args:
        token: JWT token string

    Returns:
        Expiration datetime in UTC

    Raises:
        jwt.InvalidTokenError: If token is malformed
    """
    try:
        # Decode without verification to get expiry
        payload = jwt.decode(token, options={"verify_signature": False})
        exp_timestamp = payload.get("exp")

        if exp_timestamp:
            return datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        else:
            raise jwt.InvalidTokenError("Token does not contain 'exp' field")

    except Exception as e:
        logger.error(f"Failed to get token expiry: {e}")
        raise jwt.InvalidTokenError(f"Cannot parse token expiry: {e}")


def is_token_expired(token: str) -> bool:
    """
    Check if a JWT token is expired without full verification.

    Args:
        token: JWT token string

    Returns:
        True if expired, False otherwise
    """
    try:
        exp_time = get_token_expiry(token)
        return datetime.now(timezone.utc) >= exp_time
    except Exception:
        # If we can't parse the token, consider it expired/invalid
        return True


def get_token_jti(token: str) -> Optional[str]:
    """
    Get the JTI (JWT ID) from a token without full verification.

    Args:
        token: JWT token string

    Returns:
        JTI string if present, None otherwise
    """
    try:
        # Decode without verification to get JTI
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("jti")
    except Exception as e:
        logger.error(f"Failed to get token JTI: {e}")
        return None


def decode_token_without_verification(token: str) -> Optional[Dict[str, Any]]:
    """
    Decode a JWT token without signature verification.
    Useful for getting claims from expired or untrusted tokens.

    Args:
        token: JWT token string

    Returns:
        Token payload dict if parseable, None otherwise
    """
    try:
        payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        return payload
    except Exception as e:
        logger.error(f"Failed to decode token: {e}")
        return None
