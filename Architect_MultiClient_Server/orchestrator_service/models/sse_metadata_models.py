from datetime import datetime
from typing import Annotated, Any, ClassVar, Literal
from uuid import UUID

from fastapi import Path
from pydantic import BaseModel, Field, field_validator, model_validator

from orchestrator_service.models.metadata_event_models import MetadataEventType

MetadataEventIdPath = Annotated[
    UUID,
    Path(description="Metadata event UUID"),
]


class RoomInfo(BaseModel):  # type: ignore[explicit-any]
    """Room information"""

    room_id: str = Field(..., description="Room identifier")
    room_name: str = Field(..., description="Room name")

    @field_validator("room_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            UUID(v)
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid UUID format: {v!r}") from e
        return v

    class Config:
        # TODO: Use `Any` type becase json_schema_extra is defined by complex structure
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1"}
        }


class SessionStartedRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for session_started event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1"}
        }


class SessionEndedRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for session_ended event"""

    duration_seconds: int | None = Field(None, description="Duration of room session in seconds")

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1", "duration_seconds": 3600}
        }


class FileResult(BaseModel):  # type: ignore[explicit-any]
    """Recording file result"""

    participant_identity: str = Field(..., description="Identity of participant")
    filename: str = Field(..., description="Name of the recording file")
    start_time: str = Field(..., description="Recording start time (ISO 8601)")
    end_time: str = Field(..., description="Recording end time (ISO 8601)")

    class Config:
        json_schema_extra: ClassVar[dict[str, dict[str, str]]] = {
            "example": {
                "participant_identity": "user_1",
                "filename": "user_1_audio.mp3",
                "start_time": "2026-03-02T10:00:01Z",
                "end_time": "2026-03-02T11:00:00Z",
            }
        }


class SessionRecordDoneRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for room_record_done event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Room_1"}
        }


class SessionSummaryDoneRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for room_summary_done event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "69a66008cfc00881f1d7b382", "room_name": "H3U-EXdDg"}
        }



class MetadataEventResponse(BaseModel):  # type: ignore[explicit-any]
    """Data Transfer Object for Metadata Event (distinguish with ORM model)"""

    id: str = Field(description="Event primary key")
    event_id: str | None = Field(default=None, description="Event UUID")
    event_type: str | None = Field(default=None, description="Event Type")
    room_id: str | None = Field(default=None, description="Room ID")
    room_name: str | None = Field(default=None, description="Room Name")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] | None = Field(default=None, description="Metadata")  # type: ignore[explicit-any]

    timestamp: str | None = Field(default=None, description="Timestamp")
    created_at: str | None = Field(default=None, description="Created At")


class MetadataPushResponse(BaseModel):  # type: ignore[explicit-any]
    """Response after broadcasting a metadata event."""

    status: Literal["ok"] = Field(default="ok", description="Request status")
    event_type: MetadataEventType = Field(description="Type of metadata event broadcast")
    event_id: str = Field(description="Unique identifier of the broadcast event")
    room_id: str = Field(description="Identifier of the related room")
    room_name: str = Field(description="Name of the related room")
    timestamp: str = Field(description="UTC timestamp when the event was created")
    active_connections: int = Field(description="Number of active SSE connections")
    broadcast_to: int = Field(description="Number of clients that received the event")
    duration_seconds: int | None = Field(
        default=None,
        description="Room duration in seconds; returned only for room-ended events",
    )


class MetadataEventListResponse(BaseModel):  # type: ignore[explicit-any]
    """Paginated metadata event list response."""

    status: Literal["ok"] = Field(default="ok", description="Request status")
    total: int = Field(description="Total matching metadata events")
    limit: int = Field(description="Maximum events requested per page")
    skip: int = Field(description="Number of events skipped before this page")
    ttl_seconds: int = Field(description="Retention time for metadata events in seconds")
    data: list[MetadataEventResponse] = Field(description="Metadata events in this page")


class MetadataEventDetailResponse(BaseModel):  # type: ignore[explicit-any]
    """Single metadata event response."""

    status: Literal["ok"] = Field(default="ok", description="Request status")
    data: MetadataEventResponse = Field(description="Requested metadata event")


class MetadataListQuery(BaseModel): # type: ignore[explicit-any]
    """Query parameters for listing metadata events."""

    event_type: MetadataEventType | None = Field(
        default=None,
        description="Filter by metadata event type",
    )
    room_id: str | None = Field(
        default=None,
        description="Filter by room UUID",
    )
    from_utc: datetime | None = Field(
        default=None,
        description="Only events created at or after this UTC time",
    )
    to_utc: datetime | None = Field(
        default=None,
        description="Only events created at or before this UTC time",
    )
    limit: int = Field(
        default=100,
        ge=1,
        le=1000,
        description="Maximum events returned per page",
    )
    skip: int = Field(
        default=0,
        ge=0,
        description="Number of events skipped before this page",
    )
    sort_order: Literal["asc", "desc"] = Field(
        default="desc",
        description="Sort order by creation time",
    )

    @field_validator("room_id")
    @classmethod
    def validate_room_id(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                UUID(value)
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid UUID format: {value!r}") from exc
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "MetadataListQuery":
        if self.from_utc is not None and self.to_utc is not None and self.from_utc >= self.to_utc:
            raise ValueError("from_utc must be before to_utc")
        return self
