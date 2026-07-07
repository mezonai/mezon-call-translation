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

from fastapi import APIRouter, Depends, HTTPException

from orchestrator_service.auth.jwt_auth import JWTPayload, verify_jwt
from orchestrator_service.models.auth_models import (
    BotLoginRequest,
    BotLoginResponse,
    ExchangeCodeRequest,
    ExchangeCodeResponse,
    LogoutRequest,
    RefreshTokenRequest,
    RefreshTokenResponse,
)
from orchestrator_service.services.auth_service import AuthService, get_auth_service
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

# Router
router = APIRouter(prefix="/auth", tags=["Authentication"])

@router.post("/mezon/exchange", response_model=ExchangeCodeResponse)
async def exchange_code_for_token(request: ExchangeCodeRequest, auth_service: AuthService = Depends(get_auth_service)):
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
    if not re.match(r"^[a-zA-Z0-9]{11}$", request.state):
        logger.warning(f"Invalid state parameter format: {request.state}")
        raise HTTPException(status_code=400, detail="Invalid state parameter. Must be 11 alphanumeric characters.")

    try:
        return await auth_service.exchange_code_for_token(request.code, request.state)
    except HTTPException:
        # Re-raise HTTP exceptions as-is
        raise
    except Exception as e:
        logger.error(f"Unexpected error during OAuth2 exchange: {e}")
        raise HTTPException(status_code=500, detail=f"Authentication failed: {e!s}") from e


@router.get("/mezon/userinfo")
async def get_current_user(
    user: JWTPayload = Depends(verify_jwt), auth_service: AuthService = Depends(get_auth_service)
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
    user_id = user.user_id
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token: missing user_id")
    try:
        return await auth_service.get_current_user(user_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching user info: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_access_token(request: RefreshTokenRequest, auth_service: AuthService = Depends(get_auth_service)):
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
        return await auth_service.refresh_access_token(request.refresh_token)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error refreshing token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to refresh token: {e!s}") from e


@router.post("/logout")
async def logout(
    request: LogoutRequest,
    user: JWTPayload = Depends(verify_jwt),
    auth_service: AuthService = Depends(get_auth_service),
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
    jti = user.jti
    user_id = user.user_id
    exp = user.exp

    if not jti or not user_id or exp is None:
        raise HTTPException(status_code=400, detail="Invalid token format: missing required claims")

    try:
        await auth_service.logout(
            jti=jti,
            user_id=user_id,
            exp_timestamp=exp,
            refresh_token=request.refresh_token
        )

        return {"status": "ok", "message": "Logged out successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error during logout: {e}")
        raise HTTPException(status_code=500, detail=f"Logout failed: {e!s}") from e


@router.post("/mezon/bot/login", response_model=BotLoginResponse)
async def bot_login(request: BotLoginRequest, auth_service: AuthService = Depends(get_auth_service)):
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
        return await auth_service.bot_login(request.account.model_dump())
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Bot login error: {e}")
        raise HTTPException(status_code=500, detail=f"Bot authentication failed: {e!s}") from e
