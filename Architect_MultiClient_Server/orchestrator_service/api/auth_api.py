"""
Mezon OAuth2 Authentication API

Handles OAuth2 authentication flow with Mezon:
1. Provides OAuth2 configuration to frontend
2. Exchanges authorization code for access token
3. Retrieves user information from Mezon
4. Issues JWT tokens for authenticated sessions
5. Supports token refresh and revocation (blacklist)

Endpoints:
- GET /api/auth/mezon/config - Public OAuth2 configuration
- POST /api/auth/mezon/exchange - Exchange code for JWT token + refresh token
- POST /api/auth/mezon/refresh - Refresh access token
- POST /api/auth/mezon/logout - Logout and revoke tokens
- GET /api/auth/mezon/userinfo - Get current authenticated user
"""

import os
import re
import requests
from typing import Dict, Any
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.jwt_utils import generate_jwt_token, get_token_expiry, get_token_jti
from orchestrator_service.auth.mezon_jwt_auth import verify_mezon_jwt
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.services.mongodb.refresh_token_service import RefreshTokenService
from orchestrator_service.services.mongodb.token_blacklist_service import TokenBlacklistService

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
    access_token: str = Field(..., description="JWT access token for authenticated sessions")
    refresh_token: str = Field(..., description="Refresh token for obtaining new access tokens")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")
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

        # Generate JWT access token for user session
        access_token = generate_jwt_token(user_data)

        # Get JTI from access token for refresh token linking
        access_token_jti = get_token_jti(access_token)
        if not access_token_jti:
            raise HTTPException(status_code=500, detail="Failed to generate token ID")

        # Connect to MongoDB and create refresh token
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()

        refresh_token_service = RefreshTokenService(mongodb.db)
        refresh_token = await refresh_token_service.create_refresh_token(
            user_id=user_data["user_id"],
            access_token_jti=access_token_jti,
            device_info=None  # Can be extended to capture device info from request
        )

        # Calculate token expiry in seconds (for frontend)
        token_expiry = get_token_expiry(access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"Successfully authenticated user: {user_data['username']} (ID: {user_data['user_id']})")

        return ExchangeCodeResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=expires_in,
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


# New Request/Response Models for Refresh and Logout
class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token obtained from login")


class RefreshTokenResponse(BaseModel):
    access_token: str = Field(..., description="New JWT access token")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token to revoke")


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_access_token(request: RefreshTokenRequest):
    """
    Refresh an expired access token using a valid refresh token.

    The old access token is automatically blacklisted, and a new one is issued.
    The refresh token remains valid for future refreshes until it expires.

    Args:
        request: RefreshTokenRequest with refresh_token

    Returns:
        RefreshTokenResponse with new access_token

    Raises:
        HTTPException: 401 if refresh token is invalid or expired
    """
    try:
        # Connect to MongoDB
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()

        # Validate refresh token
        refresh_token_service = RefreshTokenService(mongodb.db)
        token_doc = await refresh_token_service.validate_refresh_token(request.refresh_token)

        if not token_doc:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired refresh token. Please login again."
            )

        # Get user data from token
        user_data = {
            "user_id": token_doc["user_id"],
            # Note: We don't have username, display_name, avatar_url in refresh token doc
            # In production, you might want to fetch from user database or cache
            "username": "",
            "display_name": "",
            "avatar_url": ""
        }

        # Blacklist the old access token (if not already expired/blacklisted)
        old_jti = token_doc["access_token_jti"]
        blacklist_service = TokenBlacklistService(mongodb.db)

        # We need to get the expiry of the old token - for now,assume it's expired
        # In production, you might want to store this or calculate
        await blacklist_service.blacklist_token(
            jti=old_jti,
            user_id=user_data["user_id"],
            expires_at=datetime.now(timezone.utc),  # Already expired
            reason="refreshed"
        )

        # Generate new access token
        new_access_token = generate_jwt_token(user_data)
        new_jti = get_token_jti(new_access_token)

        if not new_jti:
            raise HTTPException(status_code=500, detail="Failed to generate new token")

        # Update refresh token doc with new access_token_jti
        await refresh_token_service.collection.update_one(
            {"token_id": token_doc["token_id"]},
            {"$set": {"access_token_jti": new_jti}}
        )

        # Calculate expiry
        token_expiry = get_token_expiry(new_access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"Access token refreshed for user_id={user_data['user_id']}")

        return RefreshTokenResponse(
            access_token=new_access_token,
            token_type="Bearer",
            expires_in=expires_in
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to refresh token: {str(e)}"
        )


@router.post("/logout")
async def logout(
    request: LogoutRequest,
    user: Dict[str, Any] = Depends(verify_mezon_jwt)
):
    """
    Logout user and revoke access token + refresh token.

    Adds the current access token to the blacklist and revokes the refresh token.
    Client should delete both tokens from storage after calling this.

    Args:
        request: LogoutRequest with refresh_token
        user: Current authenticated user from JWT

    Returns:
        Success message

    Requires:
        Authorization: Bearer <access_token>
    """
    try:
        # Connect to MongoDB
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()

        # Get JTI from current access token
        jti = user.get("jti")
        user_id = user.get("user_id")

        if not jti:
            raise HTTPException(status_code=400, detail="Invalid token format")

        # Blacklist the access token
        blacklist_service = TokenBlacklistService(mongodb.db)
        token_expiry = datetime.fromtimestamp(user.get("exp"), tz=timezone.utc)

        await blacklist_service.blacklist_token(
            jti=jti,
            user_id=user_id,
            expires_at=token_expiry,
            reason="logout"
        )

        # Revoke the refresh token
        refresh_token_service = RefreshTokenService(mongodb.db)
        await refresh_token_service.revoke_refresh_token(request.refresh_token)

        logger.info(f"User logged out: user_id={user_id}, jti={jti}")

        return {
            "status": "ok",
            "message": "Logged out successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during logout: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Logout failed: {str(e)}"
        )
