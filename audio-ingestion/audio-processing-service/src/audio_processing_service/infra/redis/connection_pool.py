"""
Shared Redis connection pool for the audio-processing-service process.

Adapted from stt_service/service/redis/connection_pool.py (audio-ingestion
PLAN.md D28 point 3 -- reuse the existing Redis Stream consumer mechanism
as-is; only import paths changed).
"""

import logging
from typing import Any, Dict, Optional

from redis.asyncio import ConnectionPool, Redis

from audio_processing_service.config import get_config
from audio_processing_service.utils.decorator import singleton

logger = logging.getLogger(__name__)


def _pool_connection_kwargs(cfg) -> Dict[str, Any]:
    return {
        "host": cfg.host,
        "port": cfg.port,
        "password": cfg.password or None,
        "db": cfg.db,
        "socket_timeout": cfg.socket_timeout,
        "socket_connect_timeout": cfg.socket_connect_timeout,
        "decode_responses": False,
    }


@singleton
class RedisConnectionManager:
    """Singleton shared Redis pool for audio-processing-service."""

    def __init__(self):
        self._config = get_config().redis
        self._pool: Optional[ConnectionPool] = None
        self._connected = False
        logger.debug("audio-processing-service RedisConnectionManager initialized")

    async def connect(self) -> None:
        if self._connected and self._pool is not None:
            return
        try:
            self._pool = ConnectionPool(
                max_connections=self._config.max_connections,
                **_pool_connection_kwargs(self._config),
            )
            client = Redis(connection_pool=self._pool)
            await client.ping()
            await client.close()
            self._connected = True
            logger.info(
                f"✅ Redis shared pool: {self._config.host}:{self._config.port} "
                f"(max_connections={self._config.max_connections})"
            )
        except Exception as e:
            logger.error(f"✗ Redis pool failed: {e}")
            self._pool = None
            self._connected = False
            raise ConnectionError(f"Redis connection failed: {e}") from e

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        self._connected = False
        logger.info("Redis connection pool closed")

    def get_pool(self) -> ConnectionPool:
        if not self._pool:
            raise RuntimeError(
                "Redis pool not initialized; call await get_connection_manager().connect() first."
            )
        return self._pool

    def get_client(self) -> Redis:
        return Redis(connection_pool=self.get_pool())

    @property
    def is_connected(self) -> bool:
        return self._connected and self._pool is not None


def get_connection_manager() -> RedisConnectionManager:
    return RedisConnectionManager()
