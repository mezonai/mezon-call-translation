"""
SSE Agent Request API
Endpoints for agents to receive requests from orchestrator via SSE
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.channels.agent_request_channel import AgentRequestChannel
from orchestrator_service.models.agent_request_payloads import (
    AgentRequestPayload,
)
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
agent_request_channel = AgentRequestChannel(sse_manager)


# ==================== Pydantic Models ====================


class SendAgentRequestBody(BaseModel):
    """Request body for sending requests to agents with discriminated union payload"""

    payload: AgentRequestPayload = Field(
        ..., discriminator="request_type", description="Request payload with type-specific schema"
    )
    room_name: Optional[str] = Field(None, description="Room name to target agents in specific room")
    agent_id: Optional[str] = Field(None, description="Agent ID to target specific agent")

    class Config:
        json_schema_extra = {
            "examples": [
                {
                    "payload": {"request_type": "transcript_control", "action": "enable"},
                    "room_name": "my-room-123",
                    "agent_id": "agent_123",
                },
                {
                    "payload": {
                        "request_type": "tts_play",
                        "text": "Hello from orchestrator",
                        "sender_identity": "orchestrator",
                        "voice": "af_heart",
                        "speed": 1.0,
                    },
                    "room_name": "my-room-123",
                    "agent_id": "agent_123",
                },
                {
                    "payload": {
                        "request_type": "send_chat_message",
                        "message": "Hello from orchestrator!",
                        "sender_name": "System Bot",
                    },
                    "room_name": "my-room-123",
                    "agent_id": "agent_123",
                },
            ]
        }


class SendAgentRequestResponse(BaseModel):
    """Response for send agent request"""

    status: str = Field(..., description="Status of operation")
    request_id: str = Field(..., description="Unique request ID")
    request_type: str = Field(..., description="Type of request")
    context: str = Field(..., description="Context key (room/agent/global)")
    active_agents: int = Field(..., description="Number of active agent connections")
    sent_to: int = Field(..., description="Number of agents that received the request")


class AgentStatusResponse(BaseModel):
    """Response for agent status check"""

    status: str = Field(..., description="Status of operation")
    context: str = Field(..., description="Context key")
    active_agents: int = Field(..., description="Number of active agents")


# ==================== SSE Endpoint ====================


@router.get("/sse/agent-requests")
async def sse_agent_requests_endpoint(agent_id: str, room_name: str, auth: Dict[str, Any] = Depends(verify_api_key)):
    """
    SSE endpoint for agents to receive requests from orchestrator.

    Agent connects to this endpoint to listen for real-time requests.
    ```
    """
    logger.info(f"[SSE Agent Request API] New agent connection request: " f"agent_id={agent_id}, room_name={room_name}")

    return await agent_request_channel.create_connection(
        agent_id=agent_id,
        room_name=room_name,
    )


# ==================== Dispatch Endpoint ====================


@router.post("/dispatch/agent-request", response_model=SendAgentRequestResponse)
async def send_agent_request(
    request: SendAgentRequestBody, auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))
):
    """
    Send request to agent(s) via SSE with type-safe payloads.

    This endpoint allows orchestrator components to send requests to agents
    that are connected via SSE. The payload is validated based on request_type
    using discriminated union.

    **Supported Request Types:**
    - `transcript_control`: Control transcription (enable/disable)
    - `tts_play`: Play TTS audio
    - `send_chat_message`: Send chat message to participants
    """
    # Extract request_type from payload
    request_type = request.payload.request_type

    # Convert payload to dict, excluding request_type (will be passed separately)
    payload_dict = request.payload.model_dump(exclude={"request_type"})

    result = await agent_request_channel.send_request(
        request_type=request_type, payload=payload_dict, room_name=request.room_name, agent_id=request.agent_id
    )

    return SendAgentRequestResponse(**result)
