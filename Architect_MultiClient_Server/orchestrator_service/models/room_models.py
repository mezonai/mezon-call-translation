from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Path
from pydantic import BaseModel, Field, field_validator, model_validator

from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.services.livekit_client import AudioTrackInfo

RoomIdPath = Annotated[
    UUID,
    Path(description="Room ID")
]

class RoomListQuery(BaseModel): # type: ignore[explicit-any]
    status: str | None = Field(
        default=None,
        min_length=VC.MIN_STATUS_LENGTH,
        max_length=VC.MAX_STATUS_LENGTH,
        description="Filter rooms by status"
    )
    search: str | None =Field(
        default=None,
        max_length=VC.MAX_SEARCH_QUERY_LENGTH,
        description="Search by room name or participant indentity"
    )
    from_utc: datetime | None = Field(
        default=None,
        description="Start of time range (UTC,ISO 8601)"
    )
    to_utc: datetime | None = Field(
        default=None,
        description="End of time range (UTC,ISO 8601)"
    )
    limit: int = Field(
        default=VC.DEFAULT_LIMIT,
        ge=VC.MIN_LIMIT,
        le=VC.MAX_LIMIT
    )
    skip: int = Field(
        default=VC.DEFAULT_SKIP,
        ge=VC.MIN_SKIP,
        le=VC.MAX_SKIP
    )
    @field_validator("search") # type: ignore[call-arg]
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        if not value:
            return None
        trimmed = value.strip()
        return trimmed if trimmed else None

    @model_validator(mode="after")  # type: ignore[call-arg]
    def validate_time_range(self) -> "RoomListQuery":
        if self.from_utc is not None and self.to_utc is not None and self.from_utc >= self.to_utc:
            raise ValueError("from_utc must be before to_utc")
        return self


class RoomData(BaseModel): # type: ignore[explicit-any]
    id: UUID
    room_name: str | None = None
    status: str | None = None
    participants: list[dict[str, Any]] | None = None # type: ignore[explicit-any]
    created_at: datetime | None = None
    finalized_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class RoomListResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"] = "ok"
    total: int
    limit: int
    skip: int
    rooms: list[RoomData]


class RoomDetailResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"] = "ok"
    room: RoomData


class RoomStatisticData(BaseModel): # type: ignore[explicit-any]
    room_id: UUID
    room_name: str | None = None
    status: str | None = None
    total_tracks: int = 0
    completed_tracks: int = 0
    remaining_tracks: int = 0
    total_segments: int = 0
    created_at: datetime | None = None
    finalized_at: datetime | None = None
    total_duration_sec: float = 0.0


class RoomStatisticResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"] = "ok"
    statistics: RoomStatisticData


class AudioInfoResponse(BaseModel): # type: ignore[explicit-any]
    status: Literal["ok"] = "ok"
    file_results: list[AudioTrackInfo]
