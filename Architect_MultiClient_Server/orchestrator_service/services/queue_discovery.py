"""
Queue Discovery - Discover available queues from Redis.

Scans Redis to find existing streams and provides queue information.
No manual registration required - automatically discovers queues.
"""

from orchestrator_service.services.redis.connection_pool import get_redis_connection
from orchestrator_service.utils.decode import decode_mapping, decode_value
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class QueueDiscovery:
    """
    Discover and list available queues from Redis.

    Scans Redis for stream keys and provides queue information
    without requiring manual registration.
    """

    @staticmethod
    async def discover_streams() -> list[str]:
        """
        Discover all Redis streams.

        Scans Redis for keys matching stream patterns and returns
        a list of stream keys.

        Returns:
            List of stream key names found in Redis
        """
        redis_client = None

        try:
            redis_client = await get_redis_connection()

            # Scan for stream keys (pattern: *:stream)
            streams: list[str] = []
            cursor = 0

            while True:
                cursor, keys = await redis_client.scan(cursor, match="*:stream", count=100)

                # Check if keys are actually streams
                for key in keys:
                    try:
                        key_type = await redis_client.type(key)
                        if decode_value(key_type) == "stream":
                            decoded_key = decode_value(key)
                            if decoded_key is not None:
                                streams.append(decoded_key)
                    except Exception as e:
                        logger.debug(f"Error checking key {key}: {e}")
                        continue

                if cursor == 0:
                    break

            logger.info(f"Discovered {len(streams)} stream(s) in Redis")
            return sorted(streams)

        except Exception as e:
            logger.error(f"Error discovering streams: {e}")
            return []
        finally:
            if redis_client is not None:
                await redis_client.close()

    @staticmethod
    async def get_stream_info(stream_key: str) -> dict | None:
        """
        Get information about a specific stream.

        Args:
            stream_key: Redis stream key

        Returns:
            Dictionary with stream info or None if not found
        """
        redis_client = None

        try:
            redis_client = await get_redis_connection()

            # Check if stream exists
            key_type = await redis_client.type(stream_key)
            if decode_value(key_type) != "stream":
                return None

            # Get stream info
            stream_length = await redis_client.xlen(stream_key)

            # Get stats if available
            stats_key = f"{stream_key}:stats"
            stats_data = await redis_client.hgetall(stats_key)      # type: ignore[misc]
            stats = decode_mapping(stats_data) if stats_data else {}

            # Count workers
            workers_key = f"{stream_key}:workers"
            workers_count = await redis_client.hlen(workers_key)    # type: ignore[misc]

            # Extract queue name from stream key
            queue_name = stream_key.replace(":stream", "")

            return {
                "queue_name": queue_name,
                "stream_key": stream_key,
                "stream_length": stream_length,
                "total_enqueued": int(stats.get("total_enqueued", 0)),
                "total_processed": int(stats.get("total_processed", 0)),
                "total_failed": int(stats.get("total_failed", 0)),
                "active_workers": workers_count,
                "exists": True,
            }

        except Exception as e:
            logger.error(f"Error getting stream info for {stream_key}: {e}")
            return None
        finally:
            if redis_client is not None:
                await redis_client.close()

    @staticmethod
    async def list_queues() -> list[dict]:
        """
        List all available queues with their info.

        Returns:
            List of dictionaries with queue information
        """
        streams = await QueueDiscovery.discover_streams()
        queues = []

        for stream_key in streams:
            info = await QueueDiscovery.get_stream_info(stream_key)
            if info:
                queues.append(info)

        return queues

    @staticmethod
    def parse_stream_key(stream_key: str) -> str:
        """
        Parse stream key to extract queue name.

        Args:
            stream_key: Redis stream key (e.g., "transcription:stream")

        Returns:
            Queue name (e.g., "transcription")
        """
        return stream_key.replace(":stream", "").split(":")[-1]

    @staticmethod
    async def queue_exists(queue_name: str) -> bool:
        """
        Check if a queue exists in Redis.

        Args:
            queue_name: Queue identifier

        Returns:
            True if queue exists, False otherwise
        """
        # Try common patterns
        possible_keys = [
            f"{queue_name}:stream",
            queue_name,
        ]

        redis_client = None

        try:
            redis_client = await get_redis_connection()

            for key in possible_keys:
                key_type = await redis_client.type(key)
                if decode_value(key_type) == "stream":
                    return True

            return False

        except Exception as e:
            logger.error(f"Error checking queue existence for {queue_name}: {e}")
            return False
        finally:
            if redis_client is not None:
                await redis_client.close()
