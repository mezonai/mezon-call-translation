"""
Shared Redis connection pool for the non-realtime STT service process.

Consumer (RedisStreamService) and producers (RedisProducerService) share one pool.
"""

import logging
from typing import Any, Dict, Optional

from redis.asyncio import ConnectionPool, Redis

from non_realtime_stt_service.config import get_config
from non_realtime_stt_service.utils.decorator import singleton

logger = logging.getLogger(__name__)


def _pool_connection_kwargs(cfg) -> Dict[str, Any]:
    """
    Per-connection kwargs for ConnectionPool.

    ``max_connections`` is set at the ``ConnectionPool(...)`` call site.
    """
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
    """Singleton shared Redis pool for non-realtime STT."""

    def __init__(self):
        self._config = get_config().redis
        self._pool: Optional[ConnectionPool] = None
        self._connected = False
        logger.debug("Non-realtime STT RedisConnectionManager initialized")

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
                f"✅ Non-realtime STT Redis shared pool: {self._config.host}:{self._config.port} "
                f"(max_connections={self._config.max_connections})"
            )
        except Exception as e:
            logger.error(f"✗ Non-realtime STT Redis pool failed: {e}")
            self._pool = None
            self._connected = False
            raise ConnectionError(f"Redis connection failed: {e}") from e

    async def disconnect(self) -> None:
        if self._pool:
            await self._pool.disconnect()
            self._pool = None
        self._connected = False
        logger.info("Non-realtime STT Redis connection pool closed")

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
