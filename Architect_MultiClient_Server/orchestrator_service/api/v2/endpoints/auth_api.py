"""
Mezon OAuth2 Authentication API

Handles OAuth2 authentication flow with Mezon:
1. Provides OAuth2 configuration to frontend
2. Exchanges authorization code for access token
3. Retrieves user information from Mezon
4. Issues JWT tokens for authenticated sessions
5. Supports token refresh and revocation (blacklist)
6. Bot account authentication

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
from orchestrator_service.auth.jwt_auth import verify_jwt
from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.services.postgresql.pg_refresh_token_repository import PgRefreshTokenRepository
from orchestrator_service.services.postgresql.pg_token_blacklist_repository import PgTokenBlacklistRepository
from orchestrator_service.services.postgresql.pg_user_permission_repository import PgUserPermissionRepository
from orchestrator_service.config.application_config import get_config
from orchestrator_service.constants.permissions import (
    DEFAULT_USER_PERMISSIONS,
    DEFAULT_BOT_PERMISSIONS
)

logger = get_logger(__name__)

# Get OAuth2 configuration from centralized config
oauth2_config = get_config().oauth2

# Validate configuration on startup
if not oauth2_config.client_id:
    logger.warning("⚠️  MEZON_CLIENT_ID is not set. OAuth2 authentication will fail!")
if not oauth2_config.client_secret:
    logger.warning("⚠️  MEZON_CLIENT_SECRET is not set. OAuth2 authentication will fail!")

logger.info(f"Mezon OAuth2 Configuration:")
logger.info(f"  - Client ID: {'configured' if oauth2_config.client_id else 'NOT SET'}")
logger.info(f"  - Client Secret: {'configured' if oauth2_config.client_secret else 'NOT SET'}")
logger.info(f"  - Redirect URI: {oauth2_config.redirect_uri}")

# Router
router = APIRouter(prefix="/auth", tags=["Authentication"])


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


@router.post("/mezon/exchange", response_model=ExchangeCodeResponse)
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
    if not oauth2_config.client_id or not oauth2_config.client_secret:
        logger.error("Mezon OAuth2 credentials not configured")
        raise HTTPException(
            status_code=500,
            detail="Mezon OAuth2 is not configured on the server."
        )

    logger.info(f"Exchanging authorization code for token (state={request.state})")

    # Debug logging to verify configuration
    logger.debug(f"Client ID: {oauth2_config.client_id if oauth2_config.client_id else 'EMPTY'}...")
    logger.debug(f"Client Secret configured: {'YES' if oauth2_config.client_secret else 'NO'}")
    logger.debug(f"Redirect URI: {oauth2_config.redirect_uri}")

    try:
        # Exchange authorization code for access token
        # Mezon uses client_secret_post method (credentials in body, not Basic Auth)
        token_data = {
            'grant_type': 'authorization_code',
            'code': request.code,
            'state': request.state,
            'client_id': oauth2_config.client_id.strip() if oauth2_config.client_id else '',
            'client_secret': oauth2_config.client_secret.strip() if oauth2_config.client_secret else '',
            'redirect_uri': oauth2_config.redirect_uri.strip() if oauth2_config.redirect_uri else ''
        }

        logger.debug("Sending token exchange request to Mezon (client_secret_post)")
        logger.debug(f"Request data keys: {list(token_data.keys())}")

        token_response = requests.post(
            oauth2_config.token_url,
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
            oauth2_config.userinfo_url,
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
            "user_id": user_info.get("user_id")
        }

        # Validate that we got a user_id
        if not user_data["user_id"]:
            logger.error(f"No user ID found in Mezon response: {user_info}")
            raise HTTPException(
                status_code=500,
                detail="Failed to retrieve user ID from Mezon"
            )

        # Connect to MongoDB
        
        # Create or update user with default permissions
        user_permission_service = PgUserPermissionRepository()

        # Check if user already exists
        existing_user = await user_permission_service.get_user_info(user_data["user_id"])

        if not existing_user:
            # First login - create user with default permissions
            logger.info(f"First login for user_id={user_data['user_id']}, assigning default user permissions")
            await user_permission_service.create_or_update_user(
                user_id=user_data["user_id"],
                username=user_info.get("username", ""),
                display_name=user_info.get("display_name", ""),
                permissions=DEFAULT_USER_PERMISSIONS,
                avatar_url=user_info.get("avatar", "")
            )
            logger.info(f"Assigned {len(DEFAULT_USER_PERMISSIONS)} default permissions to new user")
        else:
            # Existing user - just update basic info (keep existing permissions)
            await user_permission_service.create_or_update_user(
                user_id=user_data["user_id"],
                username=user_info.get("username", ""),
                display_name=user_info.get("display_name", ""),
                permissions=None,  # Don't update permissions for existing users
                avatar_url=user_info.get("avatar", "")
            )
            logger.debug(f"Updated user info for user_id={user_data['user_id']}")

        # Generate JWT access token
        access_token = generate_jwt_token(user_data)

        # Get JTI from access token for refresh token linking
        access_token_jti = get_token_jti(access_token)
        if not access_token_jti:
            raise HTTPException(status_code=500, detail="Failed to generate token ID")

        refresh_token_service = PgRefreshTokenRepository()
        refresh_token = await refresh_token_service.create_refresh_token(
            user_id=user_data["user_id"],
            access_token_jti=access_token_jti,
            device_info=None  # Can be extended to capture device info from request
        )

        # Calculate token expiry in seconds (for frontend)
        token_expiry = get_token_expiry(access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"Successfully authenticated user: {user_info['username']} (ID: {user_data['user_id']})")

        return ExchangeCodeResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=expires_in,
            user=user_info
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


@router.get("/mezon/userinfo")
async def get_current_user(user: Dict[str, Any] = Depends(verify_jwt)):
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
    user_permission_service = PgUserPermissionRepository()
    user_info = await user_permission_service.get_user_info(user.get("user_id"))

    return {
        "status": "ok",
        "user": {
            "user_id": user_info.get("user_id"),
            "username": user_info.get("username"),
            "display_name": user_info.get("display_name"),
            "avatar": user_info.get("avatar_url")
        }
    }


# New Request/Response Models for Refresh and Logout
class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token obtained from login")


class RefreshTokenResponse(BaseModel):
    access_token: str = Field(..., description="New JWT access token")
    refresh_token: str = Field(..., description="New refresh token for future access tokens")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., description="Refresh token to revoke")

class AccountModel(BaseModel):
    appid: str
    token: str

class BotLoginRequest(BaseModel):
    account: AccountModel = Field(..., description="Bot account credentials")
    class Config:
            json_schema_extra = {
                "examples": [
                    {
                        "account": {
                            "appid": "string",
                            "token": "string"
                        }
                    }
                ]
            }

class BotLoginResponse(BaseModel):
    access_token: str = Field(..., description="JWT access token for bot session")
    refresh_token: str = Field(..., description="Refresh token for obtaining new access tokens")
    token_type: str = Field(default="Bearer", description="Token type")
    expires_in: int = Field(..., description="Access token expiry in seconds")


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_access_token(request: RefreshTokenRequest):
    """
    Refresh an expired access token using a valid refresh token.

    The old access token is automatically blacklisted, and a new one is issued.
    The refresh token remains valid for future refreshes until it expires.

    Args:
        request: RefreshTokenRequest with refresh_token

    Returns:
        RefreshTokenResponse with new access_token and refresh_token

    Raises:
        HTTPException: 401 if refresh token is invalid or expired
    """
    try:
        # Connect to MongoDB
        
        # Validate refresh token
        refresh_token_service = PgRefreshTokenRepository()
        token_doc = await refresh_token_service.validate_refresh_token(request.refresh_token)

        if not token_doc:
            raise HTTPException(
                status_code=401,
                detail="Invalid or expired refresh token. Please login again."
            )

        # Get user_id from refresh token
        user_id = token_doc["user_id"]

        # Get user data from token (permissions will be loaded from DB on each request)
        user_data = {
            "user_id": user_id
        }

        # Blacklist the old access token (if not already expired/blacklisted)
        old_jti = token_doc["access_token_jti"]
        blacklist_service = PgTokenBlacklistRepository()

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

        # Rotate refresh token with new access token JTI and expiry
        new_refresh_token = await refresh_token_service.rotate_refresh_token(
            token_doc["_id"],
            new_jti
        )
        if not new_refresh_token:
            raise HTTPException(
                status_code=500,
                detail="Failed to rotate refresh token"
            )

        # Calculate expiry
        token_expiry = get_token_expiry(new_access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"Access token refreshed for user_id={user_data['user_id']}")

        return RefreshTokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
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
    user: Dict[str, Any] = Depends(verify_jwt)
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
        
        # Get JTI from current access token
        jti = user.get("jti")
        user_id = user.get("user_id")

        if not jti:
            raise HTTPException(status_code=400, detail="Invalid token format")

        # Blacklist the access token
        blacklist_service = PgTokenBlacklistRepository()
        token_expiry = datetime.fromtimestamp(user.get("exp"), tz=timezone.utc)

        await blacklist_service.blacklist_token(
            jti=jti,
            user_id=user_id,
            expires_at=token_expiry,
            reason="logout"
        )

        # Revoke the refresh token
        refresh_token_service = PgRefreshTokenRepository()
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


@router.post("/mezon/bot/login", response_model=BotLoginResponse)
async def bot_login(request: BotLoginRequest):
    """
    Authenticate a bot account with Mezon and generate JWT tokens.

    This endpoint allows bots to authenticate with their account credentials.
    It communicates with a Mezon authentication service to verify the account
    and extract user information, then generates both a Mezon JWT token and 
    our own JWT token for the bot session.

    Args:
        request: BotLoginRequest containing bot account credentials

    Returns:
        BotLoginResponse with access_token, mezon_token, and other user info

    Raises:
        HTTPException: 401 if account authentication fails, 500 on server error
    """
    try:
        logger.info("🤖 Bot login attempt")
        account = request.account.model_dump() 
        # Authenticate account with Mezon
        auth_result = await authenticate_account(account)

        if not auth_result:
            logger.warning("❌ Bot authentication failed")
            raise HTTPException(
                status_code=401,
                detail="Account authentication failed. Invalid credentials."
            )

        # Extract authentication data
        user_id = auth_result["user_id"]
        payload = auth_result["payload"]

        # Extract user info from JWT payload
        username = payload.get("usn", "")  # usn = username in Mezon JWT
        tid = payload.get("tid", "")       # tid = team id
        uid = payload.get("uid", "")       # uid = user id (numeric)

        logger.debug(f"Payload extracted: tid={tid}, uid={uid}, usn={username}")

        # Prepare user data for our JWT token
        user_data = {
            "user_id": str(user_id)
        }

        # Connect to MongoDB
        
        # Create or update bot user with default bot permissions
        user_permission_service = PgUserPermissionRepository()

        # Check if bot user already exists
        existing_bot = await user_permission_service.get_user_info(str(user_id))

        if not existing_bot:
            # First bot login - create bot user with default bot permissions
            logger.info(f"First bot login for user_id={user_id}, assigning default bot permissions")
            await user_permission_service.create_or_update_user(
                user_id=str(user_id),
                username=username,
                display_name=username,
                permissions=DEFAULT_BOT_PERMISSIONS
            )
            logger.info(f"Assigned {len(DEFAULT_BOT_PERMISSIONS)} default bot permissions")
        else:
            # Existing bot - just update basic info (keep existing permissions)
            await user_permission_service.create_or_update_user(
                user_id=str(user_id),
                username=username,
                display_name=username,
                permissions=None  # Don't update permissions for existing bots
            )
            logger.debug(f"Updated bot info for user_id={user_id}")

        # Generate our JWT access token for the bot
        access_token = generate_jwt_token(user_data)

        # Get JTI from access token for refresh token linking
        access_token_jti = get_token_jti(access_token)
        if not access_token_jti:
            raise HTTPException(status_code=500, detail="Failed to generate token ID")

        # Create refresh token (MongoDB already connected above)
        refresh_token_service = PgRefreshTokenRepository()
        refresh_token = await refresh_token_service.create_refresh_token(
            user_id=str(user_id),
            access_token_jti=access_token_jti,
            device_info=None
        )

        # Calculate token expiry in seconds (for frontend)
        token_expiry = get_token_expiry(access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"✅ Bot authenticated: user_id={user_id}, username={username}")

        return BotLoginResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="Bearer",
            expires_in=expires_in
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Bot login error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Bot authentication failed: {str(e)}"
        )
