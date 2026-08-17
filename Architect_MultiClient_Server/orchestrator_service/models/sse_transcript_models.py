from typing import Literal

from pydantic import BaseModel, Field

from orchestrator_service.models.room_registry_models import RoomNameField


class PushMessageRequest(BaseModel): # type: ignore[explicit-any]
    """Request body for pushing a transcript message through SSE."""

    room_name: RoomNameField = Field(description="LiveKit room name")
    message: str = Field(min_length=1, description="Transcript message content")
    message_type: str = Field(min_length=1, description="Transcript message type")
    participant_identity: str = Field(
        min_length=1,
        description="Identity of the participant who produced the message",
    )


class PushMessageResponse(BaseModel): # type: ignore[explicit-any]
    """Response after broadcasting a transcript message."""

    status: Literal["ok"] = Field(default="ok", description="Request status")
    room: str = Field(description="Room that received the transcript message")
    message: str = Field(description="Transcript message that was broadcast")
    message_type: str = Field(description="Type of transcript message")
    active_connections: int = Field(description="Active SSE connections in the room")
    broadcast_to: int = Field(description="Number of clients that received the message")
