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

import os
from typing import Optional, Dict, Any
from fastapi import HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from src.logger import get_logger

logger = get_logger(__name__)

# Security scheme
security = HTTPBearer(auto_error=False)

# Load configuration from environment
AUTH_ENABLED = os.getenv("TRANSCRIPT_AUTH_ENABLED", "true").lower() in ("true", "1", "yes")
API_SECRET = os.getenv("TRANSCRIPT_API_SECRET", "")

# Validate configuration on startup
if AUTH_ENABLED and not API_SECRET:
    logger.warning(
        "⚠️  TRANSCRIPT_AUTH_ENABLED is true but TRANSCRIPT_API_SECRET is not set. "
        "Authentication will fail for all requests!"
    )

logger.info(f"Transcript API Authentication: {'ENABLED' if AUTH_ENABLED else 'DISABLED'}")
if AUTH_ENABLED:
    logger.info(f"API Secret configured: {'Yes' if API_SECRET else 'No (WARNING!)'}")


def verify_simple_secret(credentials: HTTPAuthorizationCredentials) -> bool:
    """
    Verify simple secret key authentication.
    
    Args:
        credentials: HTTP Authorization credentials
        
    Returns:
        True if valid, False otherwise
    """
    if not credentials:
        return False
    
    # Simple comparison with configured secret
    return credentials.credentials == API_SECRET


def verify_jwt_token(credentials: HTTPAuthorizationCredentials) -> Optional[Dict[str, Any]]:
    """
    Verify JWT token with HMAC-SHA256.
    
    TODO: Implement JWT verification
    - Decode JWT token
    - Verify signature using HMAC-SHA256
    - Validate expiration
    - Extract claims (user_id, roles, etc.)
    
    Args:
        credentials: HTTP Authorization credentials
        
    Returns:
        Dict containing user info/claims if valid, None otherwise
    """
    # Placeholder for future JWT implementation
    # Example structure:
    # try:
    #     payload = jwt.decode(
    #         credentials.credentials,
    #         API_SECRET,
    #         algorithms=["HS256"]
    #     )
    #     return payload
    # except jwt.InvalidTokenError:
    #     return None
    
    raise NotImplementedError("JWT authentication not yet implemented")


async def verify_api_key(
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security)
) -> Dict[str, Any]:
    """
    Verify API authentication credentials.
    
    Current implementation: Simple secret key validation
    Future: Can switch to JWT validation by calling verify_jwt_token()
    
    Args:
        credentials: HTTP Authorization credentials from Bearer token
        
    Returns:
        Dict containing authentication info (can be extended with user claims)
        
    Raises:
        HTTPException: If authentication fails or is missing
    """
    # Skip authentication if disabled (for development)
    if not AUTH_ENABLED:
        logger.debug("Authentication disabled - skipping verification")
        return {"authenticated": False, "method": "disabled"}
    
    # Check if credentials provided
    if not credentials:
        logger.warning("Missing Authorization header")
        raise HTTPException(
            status_code=401,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    # Verify token/secret
    # TODO: Switch to verify_jwt_token() when implementing JWT
    is_valid = verify_simple_secret(credentials)
    
    if not is_valid:
        logger.warning("Invalid API credentials")
        raise HTTPException(
            status_code=401,
            detail="Invalid API credentials",
            headers={"WWW-Authenticate": "Bearer"}
        )
    
    logger.debug("API authentication successful")
    
    # Return authentication info
    # When implementing JWT, this can include user claims:
    # return {
    #     "authenticated": True,
    #     "method": "jwt",
    #     "user_id": claims.get("user_id"),
    #     "roles": claims.get("roles", []),
    #     "exp": claims.get("exp")
    # }
    
    return {
        "authenticated": True,
        "method": "secret"
    }

