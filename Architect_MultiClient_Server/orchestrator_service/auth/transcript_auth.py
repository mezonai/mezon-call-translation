"""
Authentication and Authorization for Transcript API endpoints.

Current implementation: Simple secret key validation
Future: Can be extended to JWT + HMAC-SHA256 validation

Environment Variables:
    TRANSCRIPT_API_SECRET: Secret key for API authentication (required)
    TRANSCRIPT_AUTH_ENABLED: Enable/disable authentication (default: true)

Usage:
    from src.auth.transcript_auth import verify_api_key
    
    @router.get("/endpoint")
    async def endpoint(auth: dict = Depends(verify_api_key)):
        # auth contains user info if needed
        pass
"""

from typing import Optional, Dict, Any
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)

# Load configuration from centralized config
auth_config = get_config().auth
API_SECRET = auth_config.transcript_api_secret

# Validate configuration on startup
if not API_SECRET:
    logger.warning(
        "⚠️  TRANSCRIPT_AUTH_ENABLED is true but TRANSCRIPT_API_SECRET is not set. "
        "Authentication will fail for all requests!"
    )

logger.info(f"Transcript API Authentication: {'ENABLED' if API_SECRET else 'DISABLED'}")
if API_SECRET:
    logger.info(f"API Secret configured: {'Yes' if API_SECRET else 'No (WARNING!)'}")


def verify_simple_secret(credentials: HTTPAuthorizationCredentials) -> bool:
    """
    Verify simple secret key authentication.
    
    Args:
        credentials: HTTP Authorization credentials
        
    Returns:
        True if valid, False otherwise
    """
    # Simple comparison with configured secret
    return credentials.credentials == API_SECRET


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Dict[str, Any]:
    """
    Verify API authentication credentials.
    Args:
        credentials: HTTP Authorization credentials from Bearer token
        
    Returns:
        Dict containing authentication info (can be extended with user claims)
        
    Raises:
        HTTPException: If authentication fails or is missing
    """
    # Check if credentials provided
    if not credentials:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"}
        )

    is_valid = verify_simple_secret(credentials)
    
    if not is_valid:
        logger.warning("Invalid API credentials")
        raise HTTPException(
            status_code=401,
            detail="Invalid API credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )

    logger.debug("API authentication successful")

    return {
        "authenticated": True,
        "method": "secret"
    }

