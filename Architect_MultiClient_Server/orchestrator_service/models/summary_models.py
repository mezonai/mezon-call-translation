"""
Pydantic models for room summary
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field

class ActionItemResult(BaseModel):
    participant_identity: str = Field(description="Participant identity")
    participant_actions: List[str] = Field(description="List of actions performed by the participant")

class SummaryActionItemsResult(BaseModel):
    summary: str = Field(description="Summary of the conversation")
    action_items: List[ActionItemResult] = Field(description="List of action items for all participants")

class RoomSummary(BaseModel):
    """Model for storing room conversation summary"""
    room_id: str
    room_name: str
    participants: List[str] = []
    summary_data: Dict[str, Any]
    full_text: str
    created_at: datetime = Field(default_factory= datetime.utcnow())
    total_segments: int = 0
    metadata: Optional[Dict[str, Any]] = None
