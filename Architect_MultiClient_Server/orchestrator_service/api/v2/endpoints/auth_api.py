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

import re
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.auth.jwt_auth import verify_jwt
from orchestrator_service.services.auth_service import AuthService, get_auth_service

logger = get_logger(__name__)

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
async def exchange_code_for_token(
    request: ExchangeCodeRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
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

    try:
        result = await auth_service.exchange_code_for_token(request.code, request.state)
        return ExchangeCodeResponse(**result)
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
async def get_current_user(
    user: Dict[str, Any] = Depends(verify_jwt),
    auth_service: AuthService = Depends(get_auth_service)
):
    """
    Get information about the currently authenticated user.

    This endpoint requires a valid JWT token in the Authorization header.
    Used by frontend to restore user session after page refresh.

    Returns:
        User information from JWT token claims

    Requires:
        Authorization: Bearer <jwt_token>
    """
    try:
        return await auth_service.get_current_user(user.get("user_id"))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
async def refresh_access_token(
    request: RefreshTokenRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
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
        result = await auth_service.refresh_access_token(request.refresh_token)
        return RefreshTokenResponse(**result)
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
    user: Dict[str, Any] = Depends(verify_jwt),
    auth_service: AuthService = Depends(get_auth_service)
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
    jti = user.get("jti")
    if not jti:
        raise HTTPException(status_code=400, detail="Invalid token format")

    try:
        await auth_service.logout(
            jti=jti,
            user_id=user.get("user_id"),
            exp_timestamp=user.get("exp"),
            refresh_token=request.refresh_token
        )

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
async def bot_login(
    request: BotLoginRequest,
    auth_service: AuthService = Depends(get_auth_service)
):
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
        result = await auth_service.bot_login(request.account.model_dump())
        return BotLoginResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Bot login error: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Bot authentication failed: {str(e)}"
        )
