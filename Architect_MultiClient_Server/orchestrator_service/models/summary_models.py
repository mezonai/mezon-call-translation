"""
Pydantic models for room summary
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class RetryType(str, Enum):
    SUMMARY = "summary"
    ACTION_ITEMS = "action_items"
    ALL = "all"


class ActionItemResult(BaseModel):
    participant_identity: str = Field(description="Participant identity")
    participant_actions: list[str] = Field(description="List of actions performed by the participant")


class SummaryResult(BaseModel):
    context: str = Field(description="Meeting context and participant permissions")
    key_discussions: str = Field(description="Main discussion details and viewpoints")
    decisions: str = Field(description="Concrete decisions or agreements")
    unresolved_issues: str = Field(description="Open issues and parking lot items")
    next_focus: str = Field(description="Expected next steps and priorities")


class ActionItemsResult(BaseModel):
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")


class SummaryActionItemsResult(BaseModel):
    summary: str = Field(description="Combined summary text of the conversation")
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")
    summary_success: bool = Field(description="Whether summary task succeeded", default=True)
    action_items_success: bool = Field(description="Whether action items task succeeded", default=True)


class RoomSummary(BaseModel):
    """Model for storing room conversation summary"""

    room_id: str = Field(description="Room ID")
    room_name: str = Field(description="Room Name", default="")
    participants: list[str] = Field(description="Participants", default=[])
    summary_data: dict[str, Any] = Field(description="Summary Data", default={})
    messages: list[dict[str, Any]] = Field(description="Messages array", default=[])
    created_at: datetime = Field(description="Created At", default_factory=datetime.utcnow)
    total_segments: int = Field(description="Total Segments", default=0)


class RoomSummaryResponse(BaseModel):
    room_id: str = Field(description="Room ID", default="")
    room_name: str = Field(description="Room Name", default="")
    participants: list[str] = Field(description="Participants", default=[])
    summary_data: dict[str, Any] = Field(description="Summary Data", default={})
    messages: list[dict[str, Any]] = Field(description="Messages array", default=[])
    created_at: str = Field(description="Created At", default="")
    completed_at: str = Field(description="Completed At", default="")
    total_segments: int = Field(description="Total Segments", default=0)
    speech_durations: list[dict[str, Any]] = Field(description="Speech Durations of each participant", default=[])
