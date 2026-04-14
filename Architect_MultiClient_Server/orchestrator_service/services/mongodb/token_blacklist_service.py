"""
Token Blacklist Service

Manages blacklisted JWT access tokens in MongoDB with the following features:
- Blacklist tokens on logout or revocation
- Automatic cleanup via MongoDB TTL index
- Fast lookup for token validation

Collections:
    token_blacklist:
        - _id: ObjectId
        - jti: JWT ID (unique identifier from token)
        - user_id: User ID who owned the token
        - token_hash: SHA256 hash of the token (optional, for extra security)
        - blacklisted_at: When the token was blacklisted
        - expires_at: When the original token expires (for TTL cleanup)
        - reason: Why blacklisted (logout, revoked, security)
"""

import hashlib
from datetime import datetime, timezone
from typing import Optional, Literal
from motor.motor_asyncio import AsyncIOMotorDatabase

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

BlacklistReason = Literal["logout", "revoked", "security", "expired"]


class TokenBlacklistService:
    """Service for managing blacklisted tokens in MongoDB"""

    def __init__(self, db: AsyncIOMotorDatabase):
        """
        Initialize token blacklist service.

        Args:
            db: MongoDB database instance
        """
        self.db = db
        self.collection = db.token_blacklist

    def _hash_token(self, token: str) -> str:
        """
        Hash a token using SHA256.

        Args:
            token: Raw JWT token

        Returns:
            Hex digest of SHA256 hash
        """
        return hashlib.sha256(token.encode()).hexdigest()

    async def blacklist_token(
        self,
        jti: str,
        user_id: str,
        expires_at: datetime,
        reason: BlacklistReason = "logout",
        token: Optional[str] = None
    ) -> bool:
        """
        Add a token to the blacklist.

        Args:
            jti: JWT ID from token claims
            user_id: User ID who owned the token
            expires_at: When the token expires (for TTL)
            reason: Reason for blacklisting
            token: Optional raw token (will be hashed)

        Returns:
            True if blacklisted successfully, False otherwise
        """
        blacklist_doc = {
            "jti": jti,
            "user_id": user_id,
            "blacklisted_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
            "reason": reason
        }

        # Optionally store token hash for extra security
        if token:
            blacklist_doc["token_hash"] = self._hash_token(token)

        try:
            await self.collection.insert_one(blacklist_doc)
            logger.info(f"Blacklisted token jti={jti}, user_id={user_id}, reason={reason}")
            return True

        except Exception as e:
            # Duplicate key error means already blacklisted - that's OK
            if "duplicate key error" in str(e).lower():
                logger.debug(f"Token jti={jti} already blacklisted")
                return True

            logger.error(f"Failed to blacklist token: {e}")
            return False

    async def is_blacklisted(self, jti: str) -> bool:
        """
        Check if a token is blacklisted.

        Args:
            jti: JWT ID from token claims

        Returns:
            True if blacklisted, False otherwise
        """
        try:
            result = await self.collection.find_one({"jti": jti})
            is_blacklisted = result is not None

            if is_blacklisted:
                logger.debug(f"Token jti={jti} is blacklisted")

            return is_blacklisted

        except Exception as e:
            logger.error(f"Failed to check blacklist: {e}")
            # Fail closed - consider it blacklisted if we can't check
            return True


0