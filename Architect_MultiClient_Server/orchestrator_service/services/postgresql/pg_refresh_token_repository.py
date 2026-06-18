"""
PostgreSQL repository for refresh tokens.
Mirrors RefreshTokenService (MongoDB) interface exactly.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from sqlalchemy import text, select, update

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import RefreshToken
from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgRefreshTokenRepository:
    """PostgreSQL-backed refresh token store. Drop-in for RefreshTokenService."""

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def generate_refresh_token(self) -> str:
        """Generate a cryptographically secure refresh token (128-char hex)."""
        return secrets.token_hex(64)

    async def create_refresh_token(
        self,
        user_id: str,
        access_token_jti: str,
        device_info: Optional[str] = None,
        expiry_days: Optional[int] = None,
    ) -> str:
        """Create and store a new refresh token. Returns the raw token."""
        refresh_token = self.generate_refresh_token()
        token_hash = self._hash_token(refresh_token)
        auth_config = get_config().auth
        expiry = expiry_days if expiry_days is not None else auth_config.refresh_token_expiry_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry)
        now = datetime.now(timezone.utc)
        token_id = uuid.uuid4()

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                new_refresh_token = RefreshToken(
                    id=token_id,
                    user_id=user_id,
                    refresh_token_hash=token_hash,
                    access_token_jti=access_token_jti,
                    expires_at=expires_at,
                    created_at=now,
                    device_info=device_info,
                    is_revoked=False,
                )
                session.add(new_refresh_token)
                await session.commit()
            logger.info(f"Created refresh token for user_id={user_id}, id={token_id}")
            return refresh_token
        except Exception as e:
            logger.error(f"Failed to create refresh token: {e}")
            raise

    async def validate_refresh_token(self, refresh_token: str) -> Optional[RefreshToken]:
        """Validate token; return token doc dict or None."""
        token_hash = self._hash_token(refresh_token)
        now = datetime.now(timezone.utc)
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    select(RefreshToken)
                    .where(
                        RefreshToken.refresh_token_hash == token_hash,
                        RefreshToken.is_revoked == False,
                        RefreshToken.expires_at > now,
                    )
                    .limit(1)
                )
                token_obj = await session.scalar(stmt)

            if token_obj:
                logger.debug(f"Refresh token validated for user_id={token_obj.user_id}")
                return token_obj
            logger.warning("Invalid or expired refresh token")
            return None
        except Exception as e:
            logger.error(f"Failed to validate refresh token: {e}")
            return None

    async def rotate_refresh_token(
        self,
        token_id: str,
        new_access_token_jti: str,
        expiry_days: Optional[int] = None,
    ) -> Optional[str]:
        """Rotate a refresh token in-place. Returns new raw token or None."""
        new_refresh_token = self.generate_refresh_token()
        token_hash = self._hash_token(new_refresh_token)
        auth_config = get_config().auth
        expiry = expiry_days if expiry_days is not None else auth_config.refresh_token_expiry_days
        expires_at = datetime.now(timezone.utc) + timedelta(days=expiry)
        now = datetime.now(timezone.utc)

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(RefreshToken)
                    .where(RefreshToken.id == token_id, RefreshToken.is_revoked == False)
                    .values(
                        refresh_token_hash=token_hash,
                        access_token_jti=new_access_token_jti,
                        expires_at=expires_at,
                        created_at=now,
                    )
                    .returning(RefreshToken.id)
                )
                result = await session.execute(stmt)
                updated_id = result.scalar()
                await session.commit()
            if updated_id:
                logger.debug(f"Rotated refresh token id={token_id}")
                return new_refresh_token
            logger.warning(f"Refresh token not found or revoked for id={token_id}")
            return None
        except Exception as e:
            logger.error(f"Failed to rotate refresh token: {e}")
            return None

    async def revoke_refresh_token(self, refresh_token: str) -> bool:
        """Revoke a specific refresh token by raw value."""
        token_hash = self._hash_token(refresh_token)
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(RefreshToken)
                    .where(RefreshToken.refresh_token_hash == token_hash)
                    .values(is_revoked=True)
                    .returning(RefreshToken.id)
                )
                result = await session.execute(stmt)
                updated_id = result.scalar()
                await session.commit()
            if updated_id:
                logger.info("Revoked refresh token")
                return True
            logger.warning("Refresh token not found for revocation")
            return False
        except Exception as e:
            logger.error(f"Failed to revoke refresh token: {e}")
            return False


# --------------- Singleton ---------------
_pg_refresh_token_repository: PgRefreshTokenRepository | None = None


def get_pg_refresh_token_repository() -> PgRefreshTokenRepository:
    global _pg_refresh_token_repository
    if _pg_refresh_token_repository is None:
        _pg_refresh_token_repository = PgRefreshTokenRepository()
    return _pg_refresh_token_repository
