"""
Pydantic models for room summary
"""

from datetime import datetime
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class RoomSummary(BaseModel):
    """Model for storing room conversation summary"""
    room_id: str
    room_name: str
    participants: List[str] = []
    summary_data: Dict[str, Any] = {}
    full_text: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    total_segments: int = 0
    metadata: Optional[Dict[str, Any]] = None
