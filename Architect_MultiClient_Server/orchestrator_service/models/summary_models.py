"""
Pydantic models for room summary
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class ActionItemResult(BaseModel):
    participant_identity: str = Field(description="Participant identity")
    participant_actions: List[str] = Field(description="List of actions performed by the participant")

class SummaryResult(BaseModel):
    context: str = Field(description="Meeting context and participant permissions")
    key_discussions: str = Field(description="Main discussion details and viewpoints")
    decisions: str = Field(description="Concrete decisions or agreements")
    unresolved_issues: str = Field(description="Open issues and parking lot items")
    next_focus: str = Field(description="Expected next steps and priorities")


class ActionItemsResult(BaseModel):
    action_items: List[ActionItemResult] = Field(description="List of action items for all participants")


class SummaryActionItemsResult(BaseModel):
    summary: str = Field(description="Combined summary text of the conversation")
    action_items: List[ActionItemResult] = Field(description="List of action items for all participants")

class RoomSummary(BaseModel):
    """Model for storing room conversation summary"""
    room_id: str = Field(description="Room ID", default="")
    room_name: str = Field(description="Room Name", default="")
    participants: List[str] = Field(description="Participants", default=[])
    summary_data: Dict[str, Any] = Field(description="Summary Data", default={})
    full_text: str = Field(description="Full Text", default="")
    created_at: datetime = Field(description="Created At", default_factory= datetime.utcnow())
    total_segments: int = Field(description="Total Segments", default=0)

class RoomSummaryResponse(BaseModel):
    room_id: str = Field(description="Room ID", default="")
    room_name: str = Field(description="Room Name", default="")
    participants: List[str] = Field(description="Participants", default=[])
    summary_data: Dict[str, Any] = Field(description="Summary Data", default={})
    full_text: str = Field(description="Full Text", default="")
    created_at: str = Field(description="Created At", default="")
    completed_at: str = Field(description="Completed At", default="")
    total_segments: int = Field(description="Total Segments", default=0)
