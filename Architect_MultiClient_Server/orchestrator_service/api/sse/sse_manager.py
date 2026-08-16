"""
Generic SSE Manager for all event channels
Manages connections, queues, and broadcasting for multiple channel types
"""

import asyncio
import threading
import time

from orchestrator_service.models.metadata_event_models import MetadataEventType
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

SSEMessage = dict[str, str | int | dict[str, str] | dict[str, int] | MetadataEventType]


@singleton
class SSEManager:
    """
    Generic Pub/Sub manager for SSE connections across multiple channels.

    Architecture:
    - Supports multiple channel types (messages, agent, chat, transcript, etc.)
    - Each channel can have different context (room-based, appid-only, etc.)
    - Each connection has its own asyncio.Queue for message delivery
    - Automatic duplicate prevention per channel based on appid + context_key

    Structure:
        channel_type -> context_key -> connection_id -> asyncio.Queue
        channel_type -> context_key -> connection_id -> appid

    Examples:
        - messages channel: context_key = "room:{room_name}"
        - agent channel: context_key = "global" (no room needed)
        - transcript channel: context_key = "room:{room_name}"
    """

    def __init__(self):
        # channel_type -> context_key -> connection_id -> asyncio.Queue
        self.connection_queues: dict[str, dict[str, dict[str, asyncio.Queue[SSEMessage]]]] = {}

        # channel_type -> context_key -> connection_id -> appid
        self.connection_appids: dict[str, dict[str, dict[str, str]]] = {}

        # Use asyncio.Lock() for async context instead of threading.Lock()
        # This prevents blocking the event loop during concurrent operations
        self._lock = asyncio.Lock()
        self._connection_counter = 0

        # Shutdown flag for signal handler (thread-safe)
        self._shutdown_flag = False
        self._shutdown_lock = threading.Lock()

    def _ensure_channel_exists(self, channel_type: str) -> None:
        """Internal: Ensure channel exists in both dictionaries"""
        if channel_type not in self.connection_queues:
            self.connection_queues[channel_type] = {}
        if channel_type not in self.connection_appids:
            self.connection_appids[channel_type] = {}

    def _ensure_context_exists(self, channel_type: str, context_key: str) -> None:
        """Internal: Ensure context exists within channel"""
        self._ensure_channel_exists(channel_type)
        if context_key not in self.connection_queues[channel_type]:
            self.connection_queues[channel_type][context_key] = {}
        if context_key not in self.connection_appids[channel_type]:
            self.connection_appids[channel_type][context_key] = {}

    async def register_connection(self, channel_type: str, context_key: str, appid: str) -> str:
        """
        Register a new connection and return unique connection_id.

        Args:
            channel_type: Type of channel (e.g., "messages", "agent", "transcript")
            context_key: Context identifier (e.g., "room:my-room", "global")
            appid: Application identifier for duplicate prevention

        Returns:
            Unique connection_id
        """
        async with self._lock:
            self._ensure_context_exists(channel_type, context_key)

            self._connection_counter += 1
            connection_id = f"{channel_type}_{context_key}_{self._connection_counter}_{int(time.time())}"

            # Track appid for duplicate prevention
            self.connection_appids[channel_type][context_key][connection_id] = appid

            logger.info(
                f"[SSE Manager] Registered connection: {connection_id} "
                f"(channel={channel_type}, context={context_key}, appid={appid})"
            )

            return connection_id

    async def create_connection_queue(
        self, channel_type: str, context_key: str, connection_id: str
    ) -> asyncio.Queue[SSEMessage]:
        """
        Create a dedicated queue for a connection.

        Args:
            channel_type: Type of channel
            context_key: Context identifier
            connection_id: Connection identifier

        Returns:
            asyncio.Queue for this connection
        """
        async with self._lock:
            self._ensure_context_exists(channel_type, context_key)

            # Create asyncio.Queue for this connection
            conn_queue: asyncio.Queue[SSEMessage] = asyncio.Queue(maxsize=100)  # Limit to prevent memory issues
            self.connection_queues[channel_type][context_key][connection_id] = conn_queue

            return conn_queue

    async def disconnect_existing_appid(self, channel_type: str, context_key: str, appid: str) -> bool:
        """
        Disconnect existing connection from the same appid in this channel/context.
        Used to prevent duplicate connections.

        Args:
            channel_type: Type of channel
            context_key: Context identifier
            appid: Application identifier

        Returns:
            True if found and disconnected existing connection
        """
        async with self._lock:
            if channel_type not in self.connection_appids or context_key not in self.connection_appids[channel_type]:
                return False

            # Find existing connection from same appid
            existing_connection_id = None
            for conn_id, aid in self.connection_appids[channel_type][context_key].items():
                if aid == appid:
                    existing_connection_id = conn_id
                    break

            if existing_connection_id:
                # Send disconnect message to client BEFORE removing queue
                if (
                    channel_type in self.connection_queues
                    and context_key in self.connection_queues[channel_type]
                    and existing_connection_id in self.connection_queues[channel_type][context_key]
                ):
                    queue = self.connection_queues[channel_type][context_key][existing_connection_id]
                    disconnect_message: SSEMessage = {
                        "event": "disconnect",
                        "data": {"message": "Duplicate connection from same appid"},
                    }
                    try:
                        # Send disconnect notification to client
                        queue.put_nowait(disconnect_message)
                        logger.info(
                            f"[SSE Manager] Sent disconnect notification to {existing_connection_id} "
                            f"(channel={channel_type}, appid={appid})"
                        )
                    except asyncio.QueueFull:
                        # Queue full, will be disconnected anyway when we remove the queue
                        logger.warning(
                            f"[SSE Manager] Queue full while disconnecting {existing_connection_id}, forcing disconnect"
                        )
                    except Exception as e:
                        logger.warning(f"[SSE Manager] Error sending disconnect to {existing_connection_id}: {e}")

                # Now remove from tracking
                self.connection_appids[channel_type][context_key].pop(existing_connection_id, None)

                # Remove queue (this will cause generator to stop)
                if channel_type in self.connection_queues and context_key in self.connection_queues[channel_type]:
                    self.connection_queues[channel_type][context_key].pop(existing_connection_id, None)

                logger.info(
                    f"[SSE Manager] Disconnected existing connection: {existing_connection_id} "
                    f"(channel={channel_type}, context={context_key}, appid={appid})"
                )

                return True

            return False

    async def unregister_connection(self, channel_type: str, context_key: str, connection_id: str):
        """
        Unregister connection when client disconnects.

        Args:
            channel_type: Type of channel
            context_key: Context identifier
            connection_id: Connection identifier
        """
        async with self._lock:
            # Remove appid tracking
            if channel_type in self.connection_appids and context_key in self.connection_appids[channel_type]:
                self.connection_appids[channel_type][context_key].pop(connection_id, None)

                # Clean up empty contexts
                if not self.connection_appids[channel_type][context_key]:
                    del self.connection_appids[channel_type][context_key]

                # Clean up empty channels
                if not self.connection_appids[channel_type]:
                    del self.connection_appids[channel_type]

            # Remove connection queue
            if channel_type in self.connection_queues and context_key in self.connection_queues[channel_type]:
                self.connection_queues[channel_type][context_key].pop(connection_id, None)

                # Clean up empty contexts
                if not self.connection_queues[channel_type][context_key]:
                    del self.connection_queues[channel_type][context_key]

                # Clean up empty channels
                if not self.connection_queues[channel_type]:
                    del self.connection_queues[channel_type]

            logger.info(
                f"[SSE Manager] Unregistered connection: {connection_id} "
                f"(channel={channel_type}, context={context_key})"
            )

    async def broadcast_message(self, channel_type: str, context_key: str, message: SSEMessage) -> int:
        """
        Broadcast message to all connections in a channel/context.

        Args:
            channel_type: Type of channel
            context_key: Context identifier
            message: Message data to broadcast

        Returns:
            Number of connections that received the message
        """
        async with self._lock:
            if channel_type not in self.connection_queues or context_key not in self.connection_queues[channel_type]:
                return 0  # No active connections

            # Get all connection queues for this channel/context
            queues = list(self.connection_queues[channel_type][context_key].values())

        # Broadcast to all queues (outside lock to avoid blocking)
        broadcast_count = 0
        for q in queues:
            try:
                # Non-blocking put - skip if queue is full (slow consumer)
                q.put_nowait(message)
                broadcast_count += 1
            except asyncio.QueueFull:
                # Skip slow consumers to prevent blocking others
                logger.warning(
                    f"[SSE Manager] Queue full, skipping slow consumer (channel={channel_type}, context={context_key})"
                )
                pass
            except Exception as e:
                logger.exception(
                    f"[SSE Manager] Error broadcasting to a connection: {e} "
                    f"(channel={channel_type}, context={context_key})"
                )
                pass

        logger.debug(
            f"[SSE Manager] Broadcasted message to {broadcast_count} connections "
            f"(channel={channel_type}, context={context_key})"
        )

        return broadcast_count

    async def has_active_connections(self, channel_type: str, context_key: str) -> bool:
        """
        Check if channel/context has active connections.

        Args:
            channel_type: Type of channel
            context_key: Context identifier

        Returns:
            True if has active connections
        """
        async with self._lock:
            return (
                channel_type in self.connection_queues
                and context_key in self.connection_queues[channel_type]
                and len(self.connection_queues[channel_type][context_key]) > 0
            )

    async def get_connection_count(self, channel_type: str, context_key: str) -> int:
        """
        Get number of active connections for channel/context.

        Args:
            channel_type: Type of channel
            context_key: Context identifier

        Returns:
            Number of active connections
        """
        async with self._lock:
            if channel_type not in self.connection_queues or context_key not in self.connection_queues[channel_type]:
                return 0
            return len(self.connection_queues[channel_type][context_key])

    async def get_stats(self) -> dict[str, dict[str, int]]:
        """
        Get statistics about all channels and contexts.

        Returns:
            Dictionary with connection counts per channel and context
        """
        async with self._lock:
            stats: dict[str, dict[str, int]] = {}
            for channel_type, contexts in self.connection_queues.items():
                stats[channel_type] = {}
                for context_key, connections in contexts.items():
                    stats[channel_type][context_key] = len(connections)
            return stats

    def signal_shutdown(self):
        """
        Synchronous method called from signal handler to initiate shutdown.
        Sends shutdown messages to all SSE connections WITHOUT using await.

        This is safe to call from signal handler because:
        - Uses threading.Lock (not asyncio.Lock)
        - Only does synchronous operations (queue.put_nowait)
        - Doesn't await anything
        """
        with self._shutdown_lock:
            if self._shutdown_flag:
                return  # Already shutting down

            self._shutdown_flag = True
            logger.info("[SSE Manager] Signal shutdown initiated, notifying all connections...")

            total_notified = 0
            shutdown_message: SSEMessage = {"event": "disconnect", "data": {"message": "Server is shutting down"}}

            # Iterate through all queues and send shutdown message
            for _, contexts in list(self.connection_queues.items()):
                for _, connections in list(contexts.items()):
                    for connection_id, queue in list(connections.items()):
                        try:
                            # Non-blocking put - if queue full, skip
                            queue.put_nowait(shutdown_message)
                            total_notified += 1
                        except asyncio.QueueFull:
                            # Queue full, connection will be disconnected anyway
                            pass
                        except Exception as e:
                            logger.warning(f"[SSE Manager] Error notifying {connection_id}: {e}")

            logger.info(f"[SSE Manager] Shutdown notifications sent to {total_notified} connections")

    async def cleanup(self):
        """
        Async cleanup method called from lifespan shutdown.
        Clears all data structures after connections have been notified.
        """
        logger.info("[SSE Manager] Starting async cleanup...")

        async with self._lock:
            total_connections = sum(
                len(connections) for contexts in self.connection_queues.values() for connections in contexts.values()
            )

            if total_connections > 0:
                logger.info(
                    f"[SSE Manager] Clearing {total_connections} connection data structures "
                    "(shutdown messages already sent from signal handler)"
                )

            # Clear all data structures
            self.connection_queues.clear()
            self.connection_appids.clear()

            logger.info(f"[SSE Manager] Cleanup completed. Cleared {total_connections} connection records")
