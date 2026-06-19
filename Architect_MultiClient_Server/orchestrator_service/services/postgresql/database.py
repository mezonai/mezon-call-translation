"""
PostgreSQL async engine and session factory.
Uses SQLAlchemy 2.0 async with asyncpg driver.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

_engine: AsyncEngine = None
_session_factory: async_sessionmaker = None


def get_engine() -> AsyncEngine:
    """Return (or lazily create) the shared async SQLAlchemy engine."""
    global _engine
    if _engine is None:
        cfg = get_config().postgresql
        _engine = create_async_engine(
            cfg.async_url,
            pool_size=cfg.pool_size,
            max_overflow=cfg.max_overflow,
            pool_pre_ping=True,  # reconnect on stale connections
            echo=False,
        )
        logger.info(
            f"PostgreSQL engine created: host={cfg.host}:{cfg.port} db={cfg.database} pool_size={cfg.pool_size}"
        )
    return _engine


def get_session_factory() -> async_sessionmaker:
    """Return (or lazily create) the async session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


async def dispose_engine():
    """Gracefully close all pooled connections. Call on app shutdown."""
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        _engine = None
        _session_factory = None
        logger.info("PostgreSQL engine disposed")
