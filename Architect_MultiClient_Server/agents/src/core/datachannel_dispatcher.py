"""
DataChannel Dispatcher - Routes DataChannel messages to appropriate handlers
"""
import asyncio
import json
import logging
from typing import Any, Callable, Optional

from src.utils.participant_identity import parse_participant_identity


logger = logging.getLogger(__name__)


def parse_json_bytes(data: bytes) -> Optional[dict]:
    """Parse JSON from bytes data."""
    try:
        if data is None:
            return None
        if isinstance(data, (bytes, bytearray)):
            s = bytes(data).decode("utf-8", errors="strict")
        else:
            s = str(data)
        return json.loads(s)
    except Exception:
        return None


class DataChannelDispatcher:
    """
    Handles DataChannel message routing.
    
    Only processes lk-chat-topic (chat messages from participants).
    Control requests are now handled via SSE.
    """
    
    def __init__(self, orchestrator_client, room_id: str, session_id: str):
        """
        Initialize dispatcher.
        
        Args:
            orchestrator_client: OrchestratorClient instance
            room_id: Room ID from orchestrator
            session_id: Session/room name
        """
        self.orchestrator = orchestrator_client
        self.room_id = room_id
        self.session_id = session_id
    
    async def handle_chat_external(self, data_packet) -> None:
        """Handle chat messages from lk-chat-topic and push to orchestrator."""
        try:
            payload = parse_json_bytes(getattr(data_packet, "data", None))
            if not payload:
                logger.warning("lk-chat-topic: invalid payload")
                return
            
            participant_id = data_packet.participant.identity if data_packet.participant else "unknown"
            participant_id = parse_participant_identity(participant_id)
            message = payload.get("message", "")
            timestamp = payload.get("timestamp") or payload.get("time")
            
            if not message:
                logger.warning(f"lk-chat-topic: empty message from {participant_id}")
                return
            
            # Push to orchestrator (broadcasts to all bots via SSE)
            if self.room_id:
                await self.orchestrator.push_chat_external(
                    room_name=self.session_id,
                    room_id=self.room_id,
                    participant_identity=participant_id,
                    message=message,
                    time_str=str(timestamp)
                )
            else:
                logger.warning("lk-chat-topic: room_id not available, skipping push")
        
        except Exception as e:
            logger.error(f"Error handling chat external: {e}", exc_info=True)
    
    def create_dispatcher(self) -> Callable:
        """
        Create DataChannel dispatcher callback.
        
        Returns:
            Callback function for room.on("data_received")
        """
        def on_data_received(data_packet):
            """
            Central DataChannel dispatcher - routes messages to appropriate handlers.
            
            Note: transcript_control, tts_play, and agent control are now handled via SSE.
            Only lk-chat-topic is processed via DataChannel.
            """
            try:
                topic = data_packet.topic
                participant_id = data_packet.participant.identity if data_packet.participant else "unknown"
                
                if topic == "lk-chat-topic":
                    logger.info(f"📩 DataChannel: lk-chat-topic from {participant_id}")
                    asyncio.create_task(self.handle_chat_external(data_packet))
                else:
                    logger.debug(f"DataChannel: Ignoring topic '{topic}' (handled via SSE or not supported)")
                    
            except Exception as e:
                logger.error(f"❌ Error in DataChannel dispatcher: {e}", exc_info=True)
        
        return on_data_received
