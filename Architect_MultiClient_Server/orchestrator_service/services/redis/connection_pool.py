"""
Shared Redis Connection Pool

Single ConnectionPool per process for all Redis usage (hash repos, streams, producer).
Uses decode_responses=False so stream workflows keep raw Redis behavior.
"""
from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger
from redis.asyncio import ConnectionPool, Redis

logger = get_logger(__name__)


@singleton
class RedisConnectionManager:
    """
    Singleton manager for the shared Redis connection pool.

    Hash repositories, RedisStreamService, and RedisProducerService all use this pool.
    """

    def __init__(self):
        """Initialize connection manager."""
        self._config = get_config().redis
        self._pool: ConnectionPool | None = None
        self._connected = False

        logger.info("RedisConnectionManager initialized")

    async def connect(self) -> None:
        """
        Create the shared connection pool.

        Raises:
            ConnectionError: If cannot connect to Redis
        """
        if self._connected and self._pool is not None:
            logger.debug("Redis connection pool already created")
            return

        try:
            self._pool = ConnectionPool(
                host=self._config.host,
                port=self._config.port,
                password=self._config.password or None,
                db=self._config.db,
                socket_timeout=self._config.socket_timeout,
                socket_connect_timeout=self._config.socket_connect_timeout,
                decode_responses=False,
                max_connections=self._config.max_connections,
            )

            redis_client = Redis(connection_pool=self._pool)
            await redis_client.ping() # type: ignore[misc]
            await redis_client.close()

            self._connected = True
            logger.info(
                f"✅ Redis shared pool: {self._config.host}:{self._config.port} "
                f"(max_connections={self._config.max_connections})"
            )

        except Exception as e:
            logger.error(f"✗ Failed to create Redis connection pool: {e}")
            self._pool = None
            self._connected = False
            raise ConnectionError(f"Redis connection failed: {e}") from e

    async def disconnect(self) -> None:
        """Close the shared connection pool."""
        if self._pool:
            await self._pool.disconnect()
            self._pool = None

        self._connected = False
        logger.info("Redis connection pool closed")

    def get_pool(self) -> ConnectionPool:
        """
        Return the shared pool for Redis(connection_pool=...).

        Raises:
            RuntimeError: If connect() has not completed successfully.
        """
        if not self._pool:
            raise RuntimeError("Redis connection pool not initialized. Call await connect() first.")
        return self._pool

    def get_client(self) -> Redis:
        """
        Get a Redis client bound to the shared pool.

        Raises:
            RuntimeError: If connection pool not initialized
        """
        return Redis(connection_pool=self.get_pool())

    @property
    def is_connected(self) -> bool:
        """Check if connection pool is active."""
        return self._connected and self._pool is not None

    async def health_check(self) -> bool:
        """
        Check if Redis connection is healthy.

        Returns:
            True if healthy, False otherwise
        """
        if not self.is_connected:
            return False

        try:
            client = self.get_client()
            await client.ping() # type: ignore[misc]
            await client.close()
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False


async def get_redis_connection() -> Redis:
    """
    Get Redis client from the shared connection pool.

    Auto-connects if not already connected.

    Returns:
        Redis client instance

    Raises:
        ConnectionError: If cannot connect to Redis
    """
    manager = RedisConnectionManager()

    if not manager.is_connected:
        await manager.connect()

    return manager.get_client()


def get_connection_manager() -> RedisConnectionManager:
    """
    Get singleton connection manager.

    Returns:
        RedisConnectionManager instance
    """
    return RedisConnectionManager()
