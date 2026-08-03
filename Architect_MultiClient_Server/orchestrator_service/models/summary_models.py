"""
Pydantic models for room summary
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


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
    completed_at: str = Field(description="Completed At", default="")
    total_segments: int = Field(description="Total Segments", default=0)

    # TODO: Use `Any` because this field consists of participant_identity and duration, where
    #  participant_identity (user_id) is retrieved from Room.participants dict in the database.
    speech_durations: list[dict[str, Any]] = Field(  # type: ignore[explicit-any]
        description="Speech Durations of each participant", default=[]
    )
