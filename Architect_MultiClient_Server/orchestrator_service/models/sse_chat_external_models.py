from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from orchestrator_service.models.room_registry_models import RoomNameField


class PushChatExternalRequest(BaseModel): # type: ignore[explicit-any]
    """Request body for pushing an external chat event through SSE."""

    room_name: RoomNameField = Field(description="Room name")
    room_id: str = Field(min_length=1, description="Room identifier")
    participant_identity: str = Field(
        min_length=1,
        description="Identity of the participant who sent the message",
    )
    message: str = Field(min_length=1, description="Chat message content")
    time: str | None = Field(
        default=None,
        min_length=1,
        description="Optional message timestamp in ISO-8601 format",
    )

    class Config:
        json_schema_extra: ClassVar[dict[str, dict[str, str]]] = {
            "example": {
                "room_name": "my-room",
                "room_id": "room-12345",
                "participant_identity": "user@example.com",
                "message": "Hello from room",
                "time": "2026-03-01T10:30:00Z",
            }
        }


class PushChatExternalResponse(BaseModel): # type: ignore[explicit-any]
    """Response after broadcasting an external chat event."""

    status: Literal["ok"] = Field(default="ok", description="Request status")
    room_name: str = Field(description="Name of the related room")
    room_id: str = Field(description="Identifier of the related room")
    participant_identity: str = Field(description="Identity of the message sender")
    message: str = Field(description="Chat message that was broadcast")
    time: str = Field(description="Timestamp assigned to the chat event")
    active_connections: int = Field(description="Number of active SSE connections")
    broadcast_to: int = Field(description="Number of clients that received the event")
