"""
Agent Request Channel for orchestrator-to-agent communication
Handles SSE connections for agents to receive requests from orchestrator
"""

import time
import uuid
from typing import Any

from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.sse_base import create_sse_response, event_generator
from orchestrator_service.api.sse.sse_manager import SSEManager, SSEMessage
from orchestrator_service.services.agents_bot_user_client import send_agents_bot_chat_message
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


@singleton
class AgentRequestChannel:
    """
    Agent Request Channel for orchestrator-to-agent communication.
    """

    CHANNEL_TYPE = "agent_requests"

    def __init__(self, manager: SSEManager):
        """
        Initialize agent request channel.

        Args:
            manager: Shared SSEManager instance
        """
        self.manager = manager

    @staticmethod
    def get_context_key(room_name: str, agent_id: str) -> str:
        """
        Get context key for agent request channel.

        Args:
            room_name: Room name for room-scoped requests
            agent_id: Agent ID for agent-scoped requests

        Returns:
            Context key in format "{room_name}:{agent_id}"
        """
        return f"{room_name}:{agent_id}"

    async def create_connection(
        self,
        agent_id: str,
        room_name: str,
    ) -> StreamingResponse:
        """
        Create SSE connection for agent to receive requests.

        Args:
            agent_id: Unique agent identifier (used as appid for duplicate prevention)
            room_name: Optional room name (agent subscribes to room-specific requests)

        Returns:
            StreamingResponse with SSE events
        """
        # Determine context based on room_name
        context_key = self.get_context_key(room_name=room_name, agent_id=agent_id)

        # Close existing connection from same agent_id in this context
        existing_disconnected = await self.manager.disconnect_existing_appid(self.CHANNEL_TYPE, context_key, agent_id)
        if existing_disconnected:
            logger.info(
                f"[Agent Request Channel] Closed existing connection for agent {agent_id} in context {context_key}"
            )

        # Register new connection
        connection_id = await self.manager.register_connection(self.CHANNEL_TYPE, context_key, agent_id)

        # Create dedicated queue
        connection_queue = await self.manager.create_connection_queue(self.CHANNEL_TYPE, context_key, connection_id)

        logger.info(
            f"[Agent Request Channel] New agent connection registered: {connection_id} "
            f"(agent_id={agent_id}, context={context_key}), "
            f"total: {await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key)}"
        )

        # Create SSE response
        return create_sse_response(
            event_generator(self.CHANNEL_TYPE, context_key, connection_id, connection_queue, self.manager)
        )

    # TODO: Use `Any` type for the payload type because the request payload has a complex structure
    async def send_request(  # type: ignore[explicit-any]
        self,
        request_type: str,
        payload: dict[str, Any],
        room_name: str,
        agent_id: str,
    ) -> dict[str, Any]:
        """
        Send request to agent(s) via SSE.

        Args:
            request_type: Type of request (transcript_control, tts_play, send_chat_message)
            payload: Request payload data
            room_name: Optional room name to target agents in specific room
            agent_id: Optional agent ID to target specific agent

        Returns:
            Dictionary with send status
        """
        # send_chat_message routes to agents-bot directly instead of the SSE
        # broadcast below -- see _send_chat_message's doc for why. This is
        # the only request_type handled this way; tts_play/transcript_control
        # keep going through the SSE path unchanged.
        if request_type == "send_chat_message":
            return await self._send_chat_message(payload=payload, room_name=room_name)

        context_key = self.get_context_key(room_name=room_name, agent_id=agent_id)

        # Check if context has active connections
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(f"[Agent Request Channel] No active agents for context {context_key}, request may be lost")

        # Prepare request data
        request_data: SSEMessage = {
            "request_id": str(uuid.uuid4()),
            "request_type": request_type,
            "timestamp": int(time.time()),
            "payload": payload,
        }

        # Broadcast request
        broadcast_count = await self.manager.broadcast_message(self.CHANNEL_TYPE, context_key, request_data)

        logger.info(
            f"[Agent Request Channel] Sent request {request_data['request_id']} "
            f"(type={request_type}, context={context_key}, sent_to={broadcast_count})"
        )

        return {
            "status": "ok",
            "request_id": request_data["request_id"],
            "request_type": request_type,
            "context": context_key,
            "active_agents": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "sent_to": broadcast_count,
        }

    async def _send_chat_message(self, payload: dict[str, Any], room_name: str) -> dict[str, Any]:
        """
        Handle send_chat_message by calling agents-bot directly rather than
        broadcasting over SSE (mezon-sfu-migration-plan.md). agents-bot --
        not the per-room Go agent -- is the only thing holding a live Mezon
        bot session, so there is no SSE agent-request listener that could
        ever act on this request_type; routing it through one first would
        just be a pass-through hop with no state of its own to add.

        Response shape matches send_request's SSE-path contract
        (SendAgentRequestResponse) for callers of the dispatch endpoint:
        "room not currently active on agents-bot" is treated the same way
        the SSE path treats "no active agent connection" -- a soft,
        expected non-delivery (status "ok", sent_to 0), not an error. Any
        other failure (agents-bot unreachable, the Mezon send itself
        failing) propagates as an exception, same as an unhandled failure
        in the SSE broadcast path above would.
        """
        request_id = str(uuid.uuid4())
        message = payload.get("message", "")

        sent = await send_agents_bot_chat_message(room_name=room_name, message=message)

        logger.info(
            f"[Agent Request Channel] send_chat_message {request_id} "
            f"(room={room_name}, sent={sent}, message_length={len(message)})"
        )

        return {
            "status": "ok",
            "request_id": request_id,
            "request_type": "send_chat_message",
            "context": room_name,
            "active_agents": 1 if sent else 0,
            "sent_to": 1 if sent else 0,
        }

    async def get_active_agents(self, room_name: str, agent_id: str) -> int:
        """
        Get count of active agents in a context.

        Args:
            room_name: Optional room name
            agent_id: Optional agent ID

        Returns:
            Number of active agent connections
        """
        context_key = self.get_context_key(room_name=room_name, agent_id=agent_id)
        return await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key)
