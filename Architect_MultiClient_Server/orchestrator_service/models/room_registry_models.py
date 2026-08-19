from typing import Annotated, ClassVar, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, field_validator

from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC

RoomNameField = Annotated[
    str,
    StringConstraints(
        min_length=VC.MIN_ROOM_NAME_LENGTH,
        max_length=VC.MAX_ROOM_NAME_LENGTH,
        pattern=VC.ROOM_NAME_PATTERN,
    )
]

class RoomRegisterRequest(BaseModel):  # type: ignore[explicit-any]
    """Request model for room registration"""

    room_name: RoomNameField = Field(..., description="Room name to register")
    room_id: str = Field(
        ...,
        description=(
            "Agent-generated stable UUID for this session (audio-ingestion "
            "PLAN.md D27x -- the agent owns its own session identity; "
            "orchestrator no longer generates it). Retrying the same "
            "room_id for the same room_name is idempotent."
        ),
    )
    @field_validator("room_id")
    @classmethod
    def validate_room_id_uuid(cls, v: str) -> str:
        try:
            UUID(v)
        except ValueError as e:
            raise ValueError("room_id must be a valid UUID string") from e
        return v

    class Config:
        json_schema_extra: ClassVar[dict[str, dict[str, str]]] = {
            "example": {
                "room_name": "my-room-123",
                "room_id": "b3f1c2a4-4e5d-4a1b-9c3e-7a2f6d8e9c10",
            }
        }


class RoomUnregisterRequest(BaseModel):  # type: ignore[explicit-any]
    """Request model for room unregistration"""

    room_name: RoomNameField = Field(..., description="Room name to unregister")
    room_id: str | None = Field(
        None,
        description=(
            "Caller's own stable room UUID from registration (audio-ingestion "
            "PLAN.md D27). If given, the registry entry for room_name is only "
            "cleared when it still points to this exact room_id -- protects "
            "against a late unregister clobbering a newer registration that "
            "reused the same room_name in the meantime."
        ),
    )
    @field_validator("room_id")
    @classmethod
    def validate_optional_room_id_uuid(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                UUID(v)
            except ValueError as e:
                raise ValueError("room_id must be a valid UUID string or None") from e
        return v


class RoomStatusResponse(BaseModel):  # type: ignore[explicit-any]
    """Response model for room status"""

    room_name: str
    registered: bool
    room_id: str | None = None


class RoomRegisterResponse(BaseModel): # type: ignore[explicit-any]
    """Response model for room registration"""
    status: Literal["ok"]
    message: str
    room_name: str
    room_id: str


class RoomUnregisterResponse(BaseModel): # type: ignore[explicit-any]
    """Response model for room unregistration"""
    status: Literal["ok"]
    message: str
    room_name: str


class RoomRegistryListResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"]
    total: int
    rooms: dict[str, str] # room_name -> room_id


class RoomRegistryClearResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"]
    message: str
    cleared_count: int
