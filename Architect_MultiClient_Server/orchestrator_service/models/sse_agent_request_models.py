from typing import Any, ClassVar

from pydantic import BaseModel, Field

from orchestrator_service.models.agent_request_payloads import AgentRequestPayload
from orchestrator_service.models.room_registry_models import RoomNameField


class SendAgentRequestBody(BaseModel):  # type: ignore[explicit-any]
    """Request body for sending requests to agents with discriminated union payload"""

    payload: AgentRequestPayload = Field(
        ..., discriminator="request_type", description="Request payload with type-specific schema"
    )
    room_name: RoomNameField = Field(
        ...,
        description="Room containing the target agent",
    )
    agent_id: str = Field(
        ...,
        min_length=1,
        description="Identifier of the target agent",
    )

    class Config:
        # TODO: Use `Any` type becase json_schema_extra is defined by complex structure
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
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


class SendAgentRequestResponse(BaseModel):  # type: ignore[explicit-any]
    """Response for send agent request"""

    status: str = Field(..., description="Status of operation")
    request_id: str = Field(..., description="Unique request ID")
    request_type: str = Field(..., description="Type of request")
    context: str = Field(..., description="Context key (room/agent/global)")
    active_agents: int = Field(..., description="Number of active agent connections")
    sent_to: int = Field(..., description="Number of agents that received the request")


class AgentStatusResponse(BaseModel):  # type: ignore[explicit-any]
    """Response for agent status check"""

    status: str = Field(..., description="Status of operation")
    context: str = Field(..., description="Context key")
    active_agents: int = Field(..., description="Number of active agents")
