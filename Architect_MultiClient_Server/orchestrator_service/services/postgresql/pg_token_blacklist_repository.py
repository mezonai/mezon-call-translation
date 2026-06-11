"""
PostgreSQL repository for token blacklist.
Mirrors TokenBlacklistService (MongoDB) interface exactly.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional, Literal

from sqlalchemy import text, select, exists
from sqlalchemy.dialects.postgresql import insert

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import TokenBlacklist
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

BlacklistReason = Literal["logout", "revoked", "security", "expired", "refreshed"]


class PgTokenBlacklistRepository:
    """PostgreSQL-backed token blacklist. Drop-in replacement for TokenBlacklistService."""

    def _hash_token(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    async def blacklist_token(
        self,
        jti: str,
        user_id: str,
        expires_at: datetime,
        reason: BlacklistReason = "logout",
        token: Optional[str] = None,
    ) -> bool:
        """Add a token to the blacklist."""
        token_hash = self._hash_token(token) if token else None
        now = datetime.now(timezone.utc)
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = insert(TokenBlacklist).values(
                    id=str(uuid.uuid4()),
                    jti=jti,
                    user_id=user_id,
                    token_hash=token_hash,
                    blacklisted_at=now,
                    expires_at=expires_at,
                    reason=reason,
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[TokenBlacklist.jti]
                )
                await session.execute(stmt)
                await session.commit()
            logger.info(f"Blacklisted token jti={jti}, user_id={user_id}, reason={reason}")
            return True
        except Exception as e:
            logger.error(f"Failed to blacklist token: {e}")
            return False

    async def is_blacklisted(self, jti: str) -> bool:
        """Return True if token jti is blacklisted."""
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(exists().where(TokenBlacklist.jti == jti))
                is_bl = await session.scalar(stmt)
            if is_bl:
                logger.debug(f"Token jti={jti} is blacklisted")
            return is_bl
        except Exception as e:
            logger.error(f"Failed to check blacklist: {e}")
            # Fail closed — treat as blacklisted if DB unavailable
            return True

# --------------- Singleton ---------------
_pg_token_blacklist_repository: PgTokenBlacklistRepository | None = None

def get_pg_token_blacklist_repository() -> PgTokenBlacklistRepository:
    global _pg_token_blacklist_repository
    if _pg_token_blacklist_repository is None:
        _pg_token_blacklist_repository = PgTokenBlacklistRepository()
    return _pg_token_blacklist_repository