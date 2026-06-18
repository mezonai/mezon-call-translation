"""
Chat External Channel for bot notifications
Handles SSE connections for chat external events (global, not room-specific)
"""

from typing import Optional, Dict, Any
from datetime import datetime
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.sse_base import event_generator, create_sse_response
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class ChatExternalChannel:
    """
    Chat External Channel for bot notifications.
    Context: global (all bots receive the same events)

    Use case: All bots connect and receive the same chat external events
    """

    CHANNEL_TYPE = "chat_external"
    CONTEXT_KEY = "chat_external_global"  # Single global context for all bots

    def __init__(self, manager: SSEManager):
        """
        Initialize chat external channel.

        Args:
            manager: Shared SSEManager instance
        """
        self.manager = manager

    async def create_connection(
        self,
        appid: str,
    ) -> StreamingResponse:
        """
        Create SSE connection for chat external channel.

        Args:
            appid: Application ID for authentication
            token: Authentication token

        Returns:
            StreamingResponse with SSE events

        Raises:
            HTTPException: If authentication fails
        """
        context_key = self.CONTEXT_KEY

        # Close existing connection from same appid
        existing_disconnected = await self.manager.disconnect_existing_appid(self.CHANNEL_TYPE, context_key, appid)
        if existing_disconnected:
            logger.info(f"[Chat External Channel] Closed existing connection for appid {appid}")

        # Register new connection
        connection_id = await self.manager.register_connection(self.CHANNEL_TYPE, context_key, appid)

        # Create dedicated queue
        connection_queue = await self.manager.create_connection_queue(self.CHANNEL_TYPE, context_key, connection_id)

        logger.info(
            f"[Chat External Channel] New connection registered: {connection_id} "
            f"(appid={appid}), "
            f"total: {await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key)}"
        )

        # Create SSE response
        return create_sse_response(
            event_generator(self.CHANNEL_TYPE, context_key, connection_id, connection_queue, self.manager)
        )

    async def push_chat_event(
        self, room_name: str, room_id: str, participant_identity: str, message: str, time: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Push chat external event to all connected bots.

        Args:
            room_name: Room name where chat occurred
            room_id: Room identifier
            participant_identity: Identity of participant who sent message
            message: Message content
            time: Optional timestamp (will use current time if not provided)

        Returns:
            Dictionary with push status
        """
        context_key = self.CONTEXT_KEY

        # Use provided time or current time
        if time is None:
            time = datetime.utcnow().isoformat()

        # Check if any bots are connected
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(f"[Chat External Channel] No active bot connections, event may be lost")

        # Prepare event data
        event_data = {
            "type": "chat_external",
            "room_name": room_name,
            "room_id": room_id,
            "participant_identity": participant_identity,
            "message": message,
            "time": time,
        }

        # Broadcast event
        broadcast_count = await self.manager.broadcast_message(self.CHANNEL_TYPE, context_key, event_data)

        logger.info(f"[Chat External Channel] Pushed event to {broadcast_count} bots " f"(room={room_name})")

        return {
            "status": "ok",
            "room_name": room_name,
            "room_id": room_id,
            "participant_identity": participant_identity,
            "message": message,
            "time": time,
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count,
        }
