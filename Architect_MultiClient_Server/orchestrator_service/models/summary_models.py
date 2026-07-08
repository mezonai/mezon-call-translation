"""
Pydantic models for room summary
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RetryType(StrEnum):
    SUMMARY = "summary"
    ACTION_ITEMS = "action_items"
    ALL = "all"


class ActionItemResult(BaseModel):          # type: ignore[explicit-any]
    participant_identity: str = Field(description="Participant identity")
    participant_actions: list[str] = Field(description="List of actions performed by the participant")


class SummaryResult(BaseModel):             # type: ignore[explicit-any]
    context: str = Field(description="Meeting context and participant permissions")
    key_discussions: str = Field(description="Main discussion details and viewpoints")
    decisions: str = Field(description="Concrete decisions or agreements")
    unresolved_issues: str = Field(description="Open issues and parking lot items")
    next_focus: str = Field(description="Expected next steps and priorities")


class ActionItemsResult(BaseModel):         # type: ignore[explicit-any]
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")


class SummaryActionItemsResult(BaseModel):  # type: ignore[explicit-any]
    summary: str = Field(description="Combined summary text of the conversation")
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")
    summary_success: bool = Field(description="Whether summary task succeeded", default=True)
    action_items_success: bool = Field(description="Whether action items task succeeded", default=True)


class RoomSummaryResponse(BaseModel):       # type: ignore[explicit-any]
    room_id: str = Field(description="Room ID", default="")
    room_name: str = Field(description="Room Name", default="")
    participants: list[str] = Field(description="Participants", default=[])

    # TODO: Use `Any` because these fields correspond to `dict[str, Any]` fields of the database `RoomSummary` model
    summary_data: dict[str, Any] = Field(description="Summary Data", default={})            # type: ignore[explicit-any]
    messages: list[dict[str, Any]] = Field(description="Messages array", default=[])        # type: ignore[explicit-any]
    created_at: str = Field(description="Created At", default="")
    completed_at: str = Field(description="Completed At", default="")
    total_segments: int = Field(description="Total Segments", default=0)

    # TODO: Use `Any` because this field consists of participant_identity and duration, where
    #  participant_identity (user_id) is retrieved from Room.participants dict in the database.
    speech_durations: list[dict[str, Any]] = Field(                                         # type: ignore[explicit-any]
        description="Speech Durations of each participant", default=[]
    )
