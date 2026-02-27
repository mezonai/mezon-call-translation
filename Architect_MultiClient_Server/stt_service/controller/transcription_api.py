"""
Transcription API Controller

Provides REST endpoints for transcription internal operations.
Note: Queue monitoring endpoints (stats, task status) are now in orchestrator_service.
"""

import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional




logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcribe", tags=["transcription"])


class RoomInfo(BaseModel):
    name: str
    room_id: str

class SessionInfo(BaseModel):
    room_name: str

class TrackMetadataRequest(BaseModel):
    """Request model for saving track metadata."""
    egress_id: str
    track_id: str
    room_ref_id: str
    participant_identity: str


class TaskStatusResponse(BaseModel):
    """Response model for task status."""
    task_id: str
    status: str
    filename: str
    created_at: float
    started_processing_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None


class QueueStatsResponse(BaseModel):
    """Response model for queue statistics."""
    queue_size: int
    total_received: int
    total_processed: int
    total_failed: int
    running: bool
    uptime: float
    pending_tasks: int
    processing_tasks: int


