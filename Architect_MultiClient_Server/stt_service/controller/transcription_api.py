"""
Transcription API Controller

Provides REST endpoints for transcription internal operations.
Note: Queue monitoring endpoints (stats, task status) are now in orchestrator_service.
"""

import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional

from ..service.redis_transcription_queue_service import (
    get_transcription_queue_service,
)



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



@router.get("/queue/stats", response_model=QueueStatsResponse)
async def get_queue_stats():
    """
    Get queue statistics.
    
    Returns:
        Queue size, processing counts, and status
    """
    queue_service = get_transcription_queue_service()
    stats = await queue_service.get_stats()
    
    return QueueStatsResponse(**stats)


@router.get("/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """
    Get status of a specific task.
    
    Args:
        task_id: The task ID returned when queuing
        
    Returns:
        Task status and result if completed
    """
    queue_service = get_transcription_queue_service()

    # Query Redis directly for task status
    task_data = await queue_service.get_task(task_id)
    
    if not task_data:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    return TaskStatusResponse(
        task_id=task_data.get("task_id", task_id),
        status=task_data.get("status", "unknown"),
        filename=task_data.get("filename", ""),
        created_at=float(task_data.get("created_at", 0)),
        started_processing_at=float(task_data.get("processing_started_at", 0)) or None,
        completed_at=float(task_data.get("completed_at", 0)) or None,
        result=task_data.get("result"),
        error=task_data.get("final_error") or task_data.get("last_error"),
    )


@router.get("/queue/pending")
async def get_pending_tasks():
    """
    Get list of pending and processing tasks.
    
    Returns:
        List of tasks that are waiting or being processed
    """
    queue_service = get_transcription_queue_service()
    pending_tasks = await queue_service.get_pending_tasks()
    return {
        "tasks": pending_tasks,
        "count": len(pending_tasks),
    }
