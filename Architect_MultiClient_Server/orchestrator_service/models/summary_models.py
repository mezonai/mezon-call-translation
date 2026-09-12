"""
Pydantic models for room summary
"""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class RetryType(StrEnum):
    SUMMARY = "summary"

    SECTIONS = "sections"
    OVERALL_CONTEXT = "overall_context"


class SummaryResult(BaseModel):  # type: ignore[explicit-any]
    context: str = Field(description="Meeting context and participant permissions")
    key_discussions: list[str] = Field(description="Main discussion details and viewpoints")
    next_focus: list[str] = Field(description="Expected next steps and priorities")
    detail: list[str] = Field(description="Detailed discussion points, decisions, and technical details")


class LightSummaryResult(BaseModel):  # type: ignore[explicit-any]
    end_message_time: str | None = Field(
        description="Timestamp of the last message in the completed section. Null if no completed topic yet."
    )
    context: str = Field(description="Meeting purpose, context, and most important outcome")
    key_discussions: list[str] = Field(description="Main discussion details and viewpoints")
    next_focus: list[str] = Field(description="Explicit action items")
    detail: list[str] = Field(description="Detailed discussion points, decisions, and technical details")


class OverallContextResult(BaseModel):  # type: ignore[explicit-any]
    context: str = Field(description="Meeting purpose, context, and most important outcome")


class RoomSummaryResponse(BaseModel):  # type: ignore[explicit-any]
    room_id: str = Field(description="Room ID", default="")
    room_name: str = Field(description="Room Name", default="")
    participants: list[str] = Field(description="Participants", default=[])

    # TODO: Use `Any` because these fields correspond to `dict[str, Any]` fields of the database `RoomSummary` model
    summary_data: dict[str, Any] = Field(description="Summary Data", default={})  # type: ignore[explicit-any]
    messages: list[dict[str, Any]] = Field(description="Messages array", default=[])  # type: ignore[explicit-any]
    created_at: str = Field(description="Created At", default="")
    finalized_at: str = Field(description="Finalized At", default="")
    completed_at: str = Field(description="Completed At", default="")
    total_segments: int = Field(description="Total Segments", default=0)

    # TODO: Use `Any` because this field consists of participant_identity and duration, where
    #  participant_identity (user_id) is retrieved from Room.participants dict in the database.
    speech_durations: list[dict[str, Any]] = Field(  # type: ignore[explicit-any]
        description="Speech Durations of each participant", default=[]
    )


class SummaryListQuery(BaseModel): # type: ignore[explicit-any]
    """Query parameters for listing summaries."""
    start_time: datetime | None = Field(
        default=None,
        description="Start time for room summary (ISO format: 2024-01-01T00:00:00Z)"
    )
    end_time: datetime | None = Field(
        default=None,
        description="End time for room summary (ISO format: 2024-01-01T00:00:00Z)"
    )

    @model_validator(mode="after") # type: ignore[call-arg]
    def validate_time_range(self) -> "SummaryListQuery":
        if self.start_time is not None and self.end_time is not None and self.start_time >= self.end_time:
            raise ValueError("start_time must be before end_time")
        return self


class SummaryListResponse(BaseModel): # type: ignore[explicit-any]
    """Response model for get_summary_by_room_name"""
    status: Literal["ok"] = "ok"
    data: list[RoomSummaryResponse]
    count: int


class SummaryDetailResponse(BaseModel): # type: ignore[explicit-any]
    """Response model for get_summary_detail_by_room_name"""
    status: Literal["ok"] = "ok"
    data: RoomSummaryResponse

class SummaryRetryRequest(BaseModel): # type: ignore[explicit-any]
    """Request body for retrying an existing summary generation."""

    type: RetryType = Field(
        default=RetryType.SUMMARY,
        description="Type of retry: summary, sections, or overall_context"
    )


class SummaryRetryResponse(BaseModel): # type: ignore[explicit-any]
    """Response model for retrying an existing summary generation."""
    status: Literal["ok"] = "ok"
    room_id: str
    type: RetryType
    summary_data: dict[str, Any] # type: ignore[explicit-any]
