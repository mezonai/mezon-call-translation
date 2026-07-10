"""
Pydantic models for room summary
"""

from datetime import datetime
from enum import Enum, StrEnum
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class RetryType(StrEnum):
    SUMMARY = "summary"
    ACTION_ITEMS = "action_items"
    ALL = "all"

    SECTIONS = "sections"
    OVERALL_CONTEXT = "overall_context"

class ActionItemResult(BaseModel):
    participant_identity: str = Field(description="Participant identity")
    participant_actions: List[str] = Field(description="List of actions performed by the participant")

class ActionItemResult(BaseModel):  # type: ignore[explicit-any]
    participant_identity: str = Field(description="Participant identity")
    participant_actions: list[str] = Field(description="List of actions performed by the participant")


class SummaryResult(BaseModel):  # type: ignore[explicit-any]
    context: str = Field(description="Meeting context and participant permissions")
    key_discussions: list[str] = Field(description="Main discussion details and viewpoints")
    next_focus: list[str] = Field(description="Expected next steps and priorities")
    detail: list[str] = Field(description="Detailed discussion points, decisions, and technical details")


class ActionItemsResult(BaseModel):  # type: ignore[explicit-any]
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")

class LightSummaryResult(BaseModel):
    end_message_time: Optional[str] = Field(description="Timestamp of the last message in the completed section. Null if no completed topic yet.")
    context: str = Field(description="Meeting purpose, context, and most important outcome")
    key_discussions: list[str] = Field(description="Main discussion details and viewpoints")
    next_focus: list[str] = Field(description="Explicit action items")
    detail: list[str] = Field(description="Detailed discussion points, decisions, and technical details")

class OverallContextResult(BaseModel):
    context: str = Field(description="Meeting purpose, context, and most important outcome")

class SummaryActionItemsResult(BaseModel):  # type: ignore[explicit-any]
    summary: str = Field(description="Combined summary text of the conversation")
    action_items: list[ActionItemResult] = Field(description="List of action items for all participants")
    summary_success: bool = Field(description="Whether summary task succeeded", default=True)
    action_items_success: bool = Field(description="Whether action items task succeeded", default=True)


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
    speech_durations: List[Dict[str, Any]] = Field(description="Speech Durations of each participant", default=[])
    
