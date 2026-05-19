"""
Refresh Token Service

Manages refresh tokens in MongoDB with the following features:
- Store refresh tokens with user association
- Automatic expiration via MongoDB TTL index
- Revoke refresh tokens
- Validate and rotate refresh tokens

Collections:
    refresh_tokens:
        - user_id: User ID from Mezon
        - refresh_token_hash: SHA256 hash of refresh token
        - access_token_jti: JTI of current access token
        - expires_at: Expiration datetime (TTL index)
        - created_at: Creation datetime
        - device_info: Optional device/client information
        - is_revoked: Boolean flag
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config

logger = get_logger(__name__)


class RefreshTokenService:
    """Service for managing refresh tokens in MongoDB"""

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize refresh token service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.collection = db.refresh_tokens


    def _hash_token(self, token: str) -> str:
        """
        Hash a refresh token using SHA256.

        Args:
            token: Raw refresh token

        Returns:
            Hex digest of SHA256 hash
        """
        return hashlib.sha256(token.encode()).hexdigest()

    def generate_refresh_token(self) -> str:
        """
        Generate a cryptographically secure refresh token.

        Returns:
            Random 64-byte hex string (128 characters)
        """
        return secrets.token_hex(64)

    async def create_refresh_token(
        self,
        user_id: str,
        access_token_jti: str,
        device_info: Optional[str] = None,
        expiry_days: Optional[int] = None
    ) -> str:
        """
        Create and store a new refresh token.

        Args:
            user_id: User ID from Mezon
            access_token_jti: JTI of the associated access token
            device_info: Optional device/client information
            expiry_days: Optional custom expiry in days 

        Returns:
            The raw refresh token (send to client, store hash in DB)
        """
        # Generate refresh token
        refresh_token = self.generate_refresh_token()
        token_hash = self._hash_token(refresh_token)

        # Calculate expiration
        auth_config = get_config().auth
        expiry = expiry_days if expiry_days is not None else auth_config.refresh_token_expiry_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry)

        # Store in database
        token_doc = {
            "user_id": user_id,
            "refresh_token_hash": token_hash,
            "access_token_jti": access_token_jti,
            "expires_at": expires_at,
            "created_at": datetime.now(timezone.utc),
            "device_info": device_info,
            "is_revoked": False
        }

        try:
            result = await self.collection.insert_one(token_doc)
            logger.info(f"Created refresh token for user_id={user_id}, _id={result.inserted_id}")
            return refresh_token

        except Exception as e:
            logger.error(f"Failed to create refresh token: {e}")
            raise

    async def validate_refresh_token(
        self,
        refresh_token: str
    ) -> Optional[Dict[str, Any]]:
        """
        Validate a refresh token and return token info if valid.

        Args:
            refresh_token: Raw refresh token from client

        Returns:
            Token document if valid, None otherwise
        """
        token_hash = self._hash_token(refresh_token)

        try:
            token_doc = await self.collection.find_one({
                "refresh_token_hash": token_hash,
                "is_revoked": False,
                "expires_at": {"$gt": datetime.now(timezone.utc)}
            })

            if token_doc:
                logger.debug(f"Refresh token validated for user_id={token_doc['user_id']}")
                return token_doc
            else:
                logger.warning("Invalid or expired refresh token")
                return None

        except Exception as e:
            logger.error(f"Failed to validate refresh token: {e}")
            return None

    async def rotate_refresh_token(
        self,
        token_id: Any,
        new_access_token_jti: str,
        expiry_days: Optional[int] = None
    ) -> Optional[str]:
        """
        Rotate a refresh token in-place and return the new raw token.

        Args:
            token_id: Refresh token document _id
            new_access_token_jti: New access token JTI
            expiry_days: Optional custom expiry in days

        Returns:
            New raw refresh token if updated, None otherwise
        """
        new_refresh_token = self.generate_refresh_token()
        token_hash = self._hash_token(new_refresh_token)
        auth_config = get_config().auth
        expiry = expiry_days if expiry_days is not None else auth_config.refresh_token_expiry_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry)

        try:
            result = await self.collection.update_one(
                {"_id": token_id, "is_revoked": False},
                {
                    "$set": {
                        "refresh_token_hash": token_hash,
                        "access_token_jti": new_access_token_jti,
                        "expires_at": expires_at,
                        "created_at": datetime.now(timezone.utc)
                    }
                }
            )

            if result.modified_count > 0:
                logger.debug(f"Rotated refresh token for _id={token_id}")
                return new_refresh_token

            if result.matched_count == 0:
                logger.warning(f"Refresh token not found or revoked for _id={token_id}")
            else:
                logger.debug(f"Refresh token rotation had no changes for _id={token_id}")
            return None

        except Exception as e:
            logger.error(f"Failed to rotate refresh token: {e}")
            return None

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        """
        Revoke a specific refresh token.

        Args:
            refresh_token: Raw refresh token to revoke

        Returns:
            True if revoked successfully, False otherwise
        """
        token_hash = self._hash_token(refresh_token)

        try:
            result = await self.collection.update_one(
                {"refresh_token_hash": token_hash},
                {"$set": {"is_revoked": True}}
            )

            if result.modified_count > 0:
                logger.info(f"Revoked refresh token")
                return True
            else:
                logger.warning("Refresh token not found for revocation")
                return False

        except Exception as e:
            logger.error(f"Failed to revoke refresh token: {e}")
            return False

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        """
        Revoke all refresh tokens for a specific user.

        Args:
            user_id: User ID

        Returns:
            Number of tokens revoked
        """
        try:
            result = await self.collection.update_many(
                {"user_id": user_id, "is_revoked": False},
                {"$set": {"is_revoked": True}}
            )

            count = result.modified_count
            logger.info(f"Revoked {count} refresh tokens for user_id={user_id}")
            return count

        except Exception as e:
            logger.error(f"Failed to revoke user tokens: {e}")
            return 0

    async def delete_refresh_token(self, refresh_token: str) -> bool:
        """
        Permanently delete a refresh token from database.

        Args:
            refresh_token: Raw refresh token to delete

        Returns:
            True if deleted, False otherwise
        """
        token_hash = self._hash_token(refresh_token)

        try:
            result = await self.collection.delete_one({
                "refresh_token_hash": token_hash
            })

            if result.deleted_count > 0:
                logger.info("Deleted refresh token")
                return True
            else:
                logger.warning("Refresh token not found for deletion")
                return False

        except Exception as e:
            logger.error(f"Failed to delete refresh token: {e}")
            return False

    async def get_user_active_tokens_count(self, user_id: str) -> int:
        """
        Get count of active (non-revoked, non-expired) refresh tokens for a user.

        Args:
            user_id: User ID

        Returns:
            Number of active tokens
        """
        try:
            count = await self.collection.count_documents({
                "user_id": user_id,
                "is_revoked": False,
                "expires_at": {"$gt": datetime.now(timezone.utc)}
            })
            return count

        except Exception as e:
            logger.error(f"Failed to count user tokens: {e}")
            return 0
