"""
SSE Agent Request API
Endpoints for agents to receive requests from orchestrator via SSE
"""


from typing import Annotated

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.channels.agent_request_channel import AgentRequestChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.models.room_registry_models import RoomNameField
from orchestrator_service.models.sse_agent_request_models import (
    SendAgentRequestBody,
    SendAgentRequestResponse,
)
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
agent_request_channel = AgentRequestChannel(sse_manager)



# ==================== SSE Endpoint ====================


@router.get("/sse/agent-requests", response_class=StreamingResponse)
async def sse_agent_requests_endpoint(
    agent_id: Annotated[str, Query(min_length=1, description="Agent identifier")],
    room_name: Annotated[RoomNameField, Query(description="Room containing the agent")],
) -> StreamingResponse:
    """
    SSE endpoint for agents to receive requests from orchestrator.

    Agent connects to this endpoint to listen for real-time requests.
    ```
    """
    logger.info(f"[SSE Agent Request API] New agent connection request: agent_id={agent_id}, room_name={room_name}")

    return await agent_request_channel.create_connection(
        agent_id=agent_id,
        room_name=room_name,
    )


# ==================== Dispatch Endpoint ====================


@router.post("/dispatch/agent-request", response_model=SendAgentRequestResponse)
async def send_agent_request(request: SendAgentRequestBody) -> SendAgentRequestResponse:
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
