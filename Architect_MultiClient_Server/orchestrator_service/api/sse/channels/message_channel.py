"""
Message Channel for transcript/translation messages
Handles SSE connections for room-based message streaming
"""
from typing import Optional, Dict, Any
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.sse_base import event_generator, create_sse_response
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class MessageChannel:
    """
    Message Channel for transcript/translation messages.
    Context: room-based (requires room parameter)
    """
    
    CHANNEL_TYPE = "messages"
    
    def __init__(self, manager: SSEManager):
        """
        Initialize message channel.
        
        Args:
            manager: Shared SSEManager instance
        """
        self.manager = manager
    
    @staticmethod
    def get_context_key(room: str) -> str:
        """
        Get context key for message channel.
        
        Args:
            room: Room name
        
        Returns:
            Context key in format "room:{room_name}"
        """
        return f"room:{room}"
    
    async def create_connection(
        self,
        appid: str,
        room: str
    ) -> StreamingResponse:
        """
        Create SSE connection for message channel.
        
        Args:
            appid: Application ID for authentication
            token: Authentication token
            room: Room name
        
        Returns:
            StreamingResponse with SSE events
        
        Raises:
            HTTPException: If authentication fails
        """
        
        context_key = self.get_context_key(room)
        
        # Close existing connection from same appid in this room
        existing_disconnected = await self.manager.disconnect_existing_appid(
            self.CHANNEL_TYPE,
            context_key,
            appid
        )
        if existing_disconnected:
            logger.info(
                f"[Message Channel] Closed existing connection for appid {appid} in room {room}"
            )
        
        # Register new connection
        connection_id = await self.manager.register_connection(
            self.CHANNEL_TYPE,
            context_key,
            appid
        )
        
        # Create dedicated queue
        connection_queue = await self.manager.create_connection_queue(
            self.CHANNEL_TYPE,
            context_key,
            connection_id
        )
        
        logger.info(
            f"[Message Channel] New connection registered: {connection_id} "
            f"(appid={appid}, room={room}), "
            f"total: {await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key)}"
        )
        
        # Create SSE response
        return create_sse_response(
            event_generator(
                self.CHANNEL_TYPE,
                context_key,
                connection_id,
                connection_queue,
                self.manager
            )
        )
    
    async def push_message(
        self,
        room: str,
        message: str,
        message_type: str,
        participant_identity: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Push message to all connections in a room.
        
        Args:
            room: Room name
            message: Message content
            message_type: Type of message
            participant_identity: Optional participant identifier
        
        Returns:
            Dictionary with push status
        """
        context_key = self.get_context_key(room)
        
        # Check if room has active connections
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(
                f"[Message Channel] No active connections for room {room}, message may be lost"
            )
        
        # Prepare message data
        message_data = {
            "message": message,
            "type": message_type,
            "participant_identity": participant_identity
        }
        
        # Broadcast message
        broadcast_count = await self.manager.broadcast_message(
            self.CHANNEL_TYPE,
            context_key,
            message_data
        )
        
        return {
            "status": "ok",
            "room": room,
            "message": message,
            "message_type": message_type,
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count
        }
