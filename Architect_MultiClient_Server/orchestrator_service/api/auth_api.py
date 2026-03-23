"""
Mezon OAuth2 Authentication API

Handles OAuth2 authentication flow with Mezon:
1. Provides OAuth2 configuration to frontend
2. Exchanges authorization code for access token
3. Retrieves user information from Mezon
4. Issues JWT tokens for authenticated sessions

Endpoints:
- GET /api/auth/mezon/config - Public OAuth2 configuration
- POST /api/auth/mezon/exchange - Exchange code for JWT token
- GET /api/auth/mezon/userinfo - Get current authenticated user
"""

import os
import re
import requests
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.jwt_utils import generate_jwt_token
from orchestrator_service.auth.mezon_jwt_auth import verify_mezon_jwt

logger = get_logger(__name__)

# OAuth2 Configuration from environment
MEZON_CLIENT_ID = os.getenv("MEZON_CLIENT_ID", "")
MEZON_CLIENT_SECRET = os.getenv("MEZON_CLIENT_SECRET", "")
MEZON_REDIRECT_URI = os.getenv("MEZON_REDIRECT_URI", "http://localhost:3000/callback")

# Mezon OAuth2 endpoints
MEZON_AUTH_URL = "https://oauth2.mezon.ai/oauth2/auth"
MEZON_TOKEN_URL = "https://oauth2.mezon.ai/oauth2/token"
MEZON_USERINFO_URL = "https://oauth2.mezon.ai/userinfo"

# Validate configuration on startup
if not MEZON_CLIENT_ID:
    logger.warning("⚠️  MEZON_CLIENT_ID is not set. OAuth2 authentication will fail!")
if not MEZON_CLIENT_SECRET:
    logger.warning("⚠️  MEZON_CLIENT_SECRET is not set. OAuth2 authentication will fail!")

logger.info(f"Mezon OAuth2 Configuration:")
logger.info(f"  - Client ID: {'configured' if MEZON_CLIENT_ID else 'NOT SET'}")
logger.info(f"  - Client Secret: {'configured' if MEZON_CLIENT_SECRET else 'NOT SET'}")
logger.info(f"  - Redirect URI: {MEZON_REDIRECT_URI}")

# Router
router = APIRouter(prefix="/api/auth/mezon", tags=["Authentication"])


# Request/Response Models
class ExchangeCodeRequest(BaseModel):
    code: str = Field(..., description="Authorization code from Mezon OAuth2 callback")
    state: str = Field(..., description="State parameter for CSRF protection (11 alphanumeric chars)")


class ExchangeCodeResponse(BaseModel):
    token: str = Field(..., description="JWT token for authenticated sessions")
    user: Dict[str, Any] = Field(..., description="User information from Mezon")


class OAuth2ConfigResponse(BaseModel):
    client_id: str = Field(..., description="Mezon OAuth2 client ID")
    auth_url: str = Field(..., description="Mezon authorization URL")
    redirect_uri: str = Field(..., description="Registered redirect URI")


@router.get("/config", response_model=OAuth2ConfigResponse)
async def get_oauth_config():
    """
    Get Mezon OAuth2 configuration for frontend.

    Returns client ID and authorization URL needed to initiate OAuth2 flow.
    Frontend uses this to build the authorization redirect URL.

    Returns:
        OAuth2ConfigResponse with client_id, auth_url, and redirect_uri
    """
    if not MEZON_CLIENT_ID:
        raise HTTPException(
            status_code=500,
            detail="Mezon OAuth2 is not configured. Please set MEZON_CLIENT_ID."
        )

    return OAuth2ConfigResponse(
        client_id=MEZON_CLIENT_ID,
        auth_url=MEZON_AUTH_URL,
        redirect_uri=MEZON_REDIRECT_URI
    )


@router.post("/exchange", response_model=ExchangeCodeResponse)
async def exchange_code_for_token(request: ExchangeCodeRequest):
    """
    Exchange authorization code for JWT token.

    After user authorizes on Mezon and is redirected back with an authorization code,
    this endpoint:
    1. Exchanges code for Mezon access token
    2. Retrieves user information from Mezon
    3. Generates a JWT token for the user session
    4. Returns JWT token and user info to frontend

    Args:
        request: ExchangeCodeRequest containing code and state

    Returns:
        ExchangeCodeResponse with JWT token and user information

    Raises:
        HTTPException: 400 if state invalid, 500 if OAuth2 exchange fails
    """
    # Validate state parameter format (11 alphanumeric characters)
    if not re.match(r'^[a-zA-Z0-9]{11}$', request.state):
        logger.warning(f"Invalid state parameter format: {request.state}")
        raise HTTPException(
            status_code=400,
            detail="Invalid state parameter. Must be 11 alphanumeric characters."
        )

    # Check OAuth2 configuration
    if not MEZON_CLIENT_ID or not MEZON_CLIENT_SECRET:
        logger.error("Mezon OAuth2 credentials not configured")
        raise HTTPException(
            status_code=500,
            detail="Mezon OAuth2 is not configured on the server."
        )

    logger.info(f"Exchanging authorization code for token (state={request.state})")

    # Debug logging to verify configuration
    logger.debug(f"Client ID (first 10 chars): {MEZON_CLIENT_ID[:10] if MEZON_CLIENT_ID else 'EMPTY'}...")
    logger.debug(f"Client Secret configured: {'YES' if MEZON_CLIENT_SECRET else 'NO'}")
    logger.debug(f"Redirect URI: {MEZON_REDIRECT_URI}")

    try:
        # Exchange authorization code for access token
        # Mezon uses client_secret_post method (credentials in body, not Basic Auth)
        token_data = {
            'grant_type': 'authorization_code',
            'code': request.code,
            'state': request.state,
            'client_id': MEZON_CLIENT_ID.strip() if MEZON_CLIENT_ID else '',
            'client_secret': MEZON_CLIENT_SECRET.strip() if MEZON_CLIENT_SECRET else '',
            'redirect_uri': MEZON_REDIRECT_URI.strip() if MEZON_REDIRECT_URI else ''
        }

        logger.debug("Sending token exchange request to Mezon (client_secret_post)")
        logger.debug(f"Request data keys: {list(token_data.keys())}")

        token_response = requests.post(
            MEZON_TOKEN_URL,
            data=token_data,  # Form-encoded data with credentials in body
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10  # 10 second timeout
        )

        if token_response.status_code != 200:
            logger.error(f"Token exchange failed: {token_response.status_code} - {token_response.text}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to exchange code for token: {token_response.text}"
            )

        token_json = token_response.json()
        access_token = token_json.get('access_token')

        if not access_token:
            logger.error(f"No access_token in response: {token_json}")
            raise HTTPException(
                status_code=500,
                detail="Mezon did not return an access token"
            )

        logger.info("Successfully obtained access token from Mezon")

        # Get user information from Mezon
        logger.debug("Fetching user info from Mezon")

        userinfo_response = requests.get(
            MEZON_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'},
            timeout=10
        )

        if userinfo_response.status_code != 200:
            logger.error(f"Failed to get user info: {userinfo_response.status_code} - {userinfo_response.text}")
            raise HTTPException(
                status_code=500,
                detail=f"Failed to get user information: {userinfo_response.text}"
            )

        user_info = userinfo_response.json()

        logger.info(f"Retrieved user info for user: {user_info.get('username', 'unknown')}")

        # Extract user data (field names may vary based on Mezon API)
        user_data = {
            "user_id": str(user_info.get("id") or user_info.get("user_id") or user_info.get("sub", "")),
            "username": user_info.get("username", ""),
            "display_name": user_info.get("display_name") or user_info.get("name", ""),
            "avatar_url": user_info.get("avatar_url") or user_info.get("picture", "")
        }

        # Validate that we got a user_id
        if not user_data["user_id"]:
            logger.error(f"No user ID found in Mezon response: {user_info}")
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve user ID from Mezon"
            )

        # Generate JWT token for user session
        jwt_token = generate_jwt_token(user_data)

        logger.info(f"Successfully authenticated user: {user_data['username']} (ID: {user_data['user_id']})")

        return ExchangeCodeResponse(
            token=jwt_token,
            user=user_data
        )

    except requests.RequestException as e:
        logger.error(f"Network error during OAuth2 exchange: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Network error communicating with Mezon: {str(e)}"
        )
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error during OAuth2 exchange: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Authentication failed: {str(e)}"
        )


@router.get("/userinfo")
async def get_current_user(user: Dict[str, Any] = Depends(verify_mezon_jwt)):
    """
    Get information about the currently authenticated user.

    This endpoint requires a valid JWT token in the Authorization header.
    Used by frontend to restore user session after page refresh.

    Returns:
        User information from JWT token claims

    Requires:
        Authorization: Bearer <jwt_token>
    """
    logger.debug(f"Returning user info for user_id={user.get('user_id')}")

    return {
        "status": "ok",
        "user": {
            "user_id": user.get("user_id"),
            "username": user.get("username"),
            "display_name": user.get("display_name"),
            "avatar_url": user.get("avatar_url")
        }
    }
