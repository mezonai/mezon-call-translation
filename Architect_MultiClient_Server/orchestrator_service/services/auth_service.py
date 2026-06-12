import requests
import re
import requests
from typing import Dict, Any, Optional
from datetime import datetime, timezone
from fastapi import HTTPException

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.jwt_utils import generate_jwt_token, get_token_expiry, get_token_jti
from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.config.application_config import get_config
from orchestrator_service.constants.permissions import DEFAULT_USER_PERMISSIONS, DEFAULT_BOT_PERMISSIONS
from orchestrator_service.services.postgresql.pg_refresh_token_repository import PgRefreshTokenRepository, get_pg_refresh_token_repository
from orchestrator_service.services.postgresql.pg_token_blacklist_repository import PgTokenBlacklistRepository, get_pg_token_blacklist_repository
from orchestrator_service.services.postgresql.pg_user_permission_repository import PgUserPermissionRepository, get_pg_user_permission_repository

logger = get_logger(__name__)

class AuthService:
    def __init__ (
        self,
        user_repo: PgUserPermissionRepository,
        refresh_token_repo: PgRefreshTokenRepository,
        blacklist_repo: PgTokenBlacklistRepository,
    ):
        self.user_repo = user_repo
        self.refresh_token_repo = refresh_token_repo
        self.blacklist_repo = blacklist_repo
        self.oauth2_config = get_config().oauth2

        missing_configs = []
        if not self.oauth2_config.client_id:
            missing_configs.append("MEZON_CLIENT_ID")
        if not self.oauth2_config.client_secret:
            missing_configs.append("MEZON_CLIENT_SECRET")

        if missing_configs:
            raise ValueError(f"CRITICAL CONFIG MISSING: {', '.join(missing_configs)} is not set. OAuth2 authentication will fail!")

        logger.info(f"Mezon OAuth2 Configuration:")
        logger.info(f"  - Client ID: configured")
        logger.info(f"  - Client Secret: configured")
        logger.info(f"  - Redirect URI: {self.oauth2_config.redirect_uri}")

    async def exchange_code_for_token(self, code: str, state: str) -> Dict[str, Any]:
        try:
            # Exchange authorization code for access token
            # Mezon uses client_secret_post method (credentials in body, not Basic Auth)
            token_data = {
                'grant_type': 'authorization_code',
                'code': code,
                'state': state,
                'client_id': self.oauth2_config.client_id.strip() if self.oauth2_config.client_id else '',
                'client_secret': self.oauth2_config.client_secret.strip() if self.oauth2_config.client_secret else '',
                'redirect_uri': self.oauth2_config.redirect_uri.strip() if self.oauth2_config.redirect_uri else '',
            }

            logger.debug("Sending token exchange request to Mezon (client_secret_post)")
            logger.debug(f"Request data keys: {list(token_data.keys())}")

            token_response = requests.post(
                self.oauth2_config.token_url,
                data=token_data, # Form-encoded data with credentials in body
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
                self.oauth2_config.userinfo_url,
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
            user_id = user_info.get('user_id')

            # Validate that we got a user_id
            if not user_id:
                logger.error(f"No user ID found in Mezon response: {user_info}")
                raise HTTPException(
                    status_code=500,
                    detail="Failed to retrieve user ID from Mezon"
                )

            # Check if user already exists
            existing_user = await self.user_repo.get_user_info(user_id)
            
            if not existing_user:
                # First login - create user with default permissions
                logger.info(f"First login for user_id={user_id}, assigning default user permissions")
                await self.user_repo.create_or_update_user(
                    user_id=user_id,
                    username=user_info.get("username", ""),
                    display_name=user_info.get("display_name", ""),
                    permissions=DEFAULT_USER_PERMISSIONS,
                    avatar_url=user_info.get("avatar", "")
                )
                logger.info(f"Assigned {len(DEFAULT_USER_PERMISSIONS)} default permissions to new user")
            else:
                # Existing user - just update basic info (keep existing permissions)
                await self.user_repo.create_or_update_user(
                    user_id=user_id,
                    username=user_info.get("username", ""),
                    display_name=user_info.get("display_name", ""),
                    permissions=None,   # Don't update permissions for existing users
                    avatar_url=user_info.get("avatar", "")
                )
                logger.debug(f"Updated user info for user_id={user_id}")

            user_data = {"user_id": user_id}

            # Generate JWT access token
            access_token = generate_jwt_token(user_data)

            # Get JTI from access token for refresh token linking
            access_token_jti = get_token_jti(access_token)

            if not access_token_jti:
                raise HTTPException(status_code=500, detail="Failed to generate token ID")

            refresh_token = await self.refresh_token_repo.create_refresh_token(
                user_id=user_id,
                access_token_jti=access_token_jti,
                device_info=None
            )

            token_expiry = get_token_expiry(access_token)
            expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

            logger.info(f"Successfully authenticated user: {user_info['username']} (ID: {user_data['user_id']})")

            return {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": expires_in,
                "user": user_info
            }

        except requests.RequestException as e:
            logger.error(f"Network error during OAuth2 exchange: {e}")
            raise HTTPException(
                status_code=500,
                detail=f"Network error communicating with Mezon: {str(e)}"
            )

    async def get_current_user(self, user_id: str) -> Dict[str, Any]:
        user_info = await self.user_repo.get_user_info(user_id)
        if not user_info:
            raise HTTPException(status_code=404, detail="User not found")

        return {
            "status": "ok",
            "user": {
                "user_id": user_info.id,
                "username": user_info.username,
                "display_name": user_info.display_name,
                "avatar": user_info.avatar_url
            }
        }

    async def refresh_access_token(self, refresh_token: str) -> Dict[str, Any]:
        # Validate refresh token
        token_doc = await self.refresh_token_repo.validate_refresh_token(refresh_token)
        if not token_doc:
            raise HTTPException(status_code=401, detail="Invalid or expired refresh token. Please login again.")

        # Get user_id from refresh token
        user_id = token_doc.user_id

        # Get user data from token (permissions will be loaded from DB on each request)
        user_data = {
            "user_id": user_id
        }

        # Blacklist the old access token (if not already expired/blacklisted)
        old_jti = token_doc.access_token_jti

        # We need to get the expiry of the old token - for now,assume it's expired
        # In production, you might want to store this or calculate
        await self.blacklist_repo.blacklist_token(
            jti=old_jti,
            user_id=user_id,
            expires_at=datetime.now(timezone.utc),
            reason="refreshed"
        )

        # Generate new access token
        new_access_token = generate_jwt_token(user_data)
        new_jti = get_token_jti(new_access_token)

        if not new_jti:
            raise HTTPException(status_code=500, detail="Failed to generate new token")

        # Rotate refresh token with new access token JTI and expiry
        new_refresh_token = await self.refresh_token_repo.rotate_refresh_token(
            token_doc.id,
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

        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "Bearer",
            "expires_in": expires_in
        }

    async def logout(self, jti: str, user_id: str, exp_timestamp: int, refresh_token: str):
        token_expiry = datetime.fromtimestamp(exp_timestamp, tz=timezone.utc)
        await self.blacklist_repo.blacklist_token(
            jti=jti,
            user_id=user_id,
            expires_at=token_expiry,
            reason="logout"
        )

        # Revoke the refresh token
        await self.refresh_token_repo.revoke_refresh_token(refresh_token)

    async def bot_login(self, account_dict: dict) -> Dict[str, Any]:
        # Authenticate account with Mezon
        auth_result = await authenticate_account(account_dict)
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
        username = payload.get("usn", "")   # usn = username in Mezon JWT
        tid = payload.get("tid", "")        # tid = team id
        uid = payload.get("uid", "")        # uid = user id (numeric)

        logger.debug(f"Payload extracted: tid={tid}, uid={uid}, usn={username}")

        # Prepare user data for our JWT token
        user_data = {
            "user_id": str(user_id)
        }

        # Check if bot user already exists
        existing_bot = await self.user_repo.get_user_info(str(user_id))

        if not existing_bot:
            # First bot login - create bot user with default bot permissions
            logger.info(f"First bot login for user_id={user_id}, assigning default bot permissions")
            await self.user_repo.create_or_update_user(
                user_id=str(user_id),
                username=username,
                display_name=username,
                permissions=DEFAULT_BOT_PERMISSIONS
            )
            logger.info(f"Assigned {len(DEFAULT_BOT_PERMISSIONS)} default bot permissions")
        else:
            # Existing bot - just update basic info (keep existing permissions)
            await self.user_repo.create_or_update_user(
                user_id=str(user_id),
                username=username,
                display_name=username,
                permissions=None    # Don't update permissions for existing bots
            )
            logger.debug(f"Updated bot info for user_id={user_id}")
        
        user_data = {"user_id": str(user_id)}

        # Generate our JWT access token for the bot
        access_token = generate_jwt_token(user_data)

        # Get JTI from access token for refresh token linking
        access_token_jti = get_token_jti(access_token)
        if not access_token_jti:
            raise HTTPException(status_code=500, detail="Failed to generate token ID")

        # Create refresh token
        refresh_token = await self.refresh_token_repo.create_refresh_token(
            user_id=str(user_id),
            access_token_jti=access_token_jti,
            device_info=None
        )

        # Calculate token expiry in seconds (for frontend)
        token_expiry = get_token_expiry(access_token)
        expires_in = int((token_expiry - datetime.now(timezone.utc)).total_seconds())

        logger.info(f"✅ Bot authenticated: user_id={user_id}, username={username}")

        return {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "expires_in": expires_in
        }

# Get singleton instances
_auth_service: AuthService | None = None

def get_auth_service() -> AuthService:
    global _auth_service
    if _auth_service is None:
        _auth_service = AuthService(
            user_repo=get_pg_user_permission_repository(),
            refresh_token_repo=get_pg_refresh_token_repository(),
            blacklist_repo=get_pg_token_blacklist_repository()
        )
    return _auth_service