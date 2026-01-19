"""
Transcription API Controller

Provides REST endpoints for transcription queue management.
"""

import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from typing import Optional

from ..service.transcription_queue_service import (
    get_transcription_queue_service,
    TranscriptionTask,
    TaskStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcribe", tags=["transcription"])


class RoomInfo(BaseModel):
    name: str

class ParticipantInfo(BaseModel):
    identity: str

class TrackInfo(BaseModel):
    id: str
    type: str
    source: str

class AudioInfo(BaseModel):
    filename: str
    location: str
    duration: str

class TimelineInfo(BaseModel):
    startedAt: str
    endedAt: str

class TranscriptionRequest(BaseModel):
    """Request model for queueing a transcription task (nested structure)."""
    egressId: str
    room: RoomInfo
    participant: ParticipantInfo
    track: TrackInfo
    audio: AudioInfo
    timeline: TimelineInfo


class TranscriptionResponse(BaseModel):
    """Response model for queued task."""
    success: bool
    task_id: str
    message: str
    queue_size: int


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


@router.post("/queue", response_model=TranscriptionResponse)
async def queue_transcription(request: TranscriptionRequest):
    """
    Queue a transcription task for processing.
    
    The task will be added to an async queue and processed by a background consumer.
    This endpoint returns immediately without blocking.
    
    Returns:
        Task ID and queue information
    """
    try:
        queue_service = get_transcription_queue_service()
        
        # Extract fields from nested request
        task = TranscriptionTask(
            filename=request.audio.filename,
            started_at=request.timeline.startedAt,
            ended_at=request.timeline.endedAt,
            duration=request.audio.duration,
            size=None,  # Not provided in new request, set None or add if needed
            location=request.audio.location,
            egress_id=request.egressId,
            room_name=request.room.name,
            participant_identity=request.participant.identity,
            track_id=request.track.id,
            track_type=request.track.type,
            track_source=request.track.source,
        )
        
        # Enqueue (non-blocking)
        try:
            task_id = queue_service.enqueue_nowait(task)
        except Exception:
            # Queue full, use blocking version with timeout
            task_id = await queue_service.enqueue(task)
        
        stats = queue_service.get_stats()
        
        return TranscriptionResponse(
            success=True,
            task_id=task_id,
            message="Task queued successfully",
            queue_size=stats["queue_size"],
        )
        
    except Exception as e:
        logger.error(f"Failed to queue transcription task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to queue task: {str(e)}"
        )


@router.get("/queue/stats", response_model=QueueStatsResponse)
async def get_queue_stats():
    """
    Get queue statistics.
    
    Returns:
        Queue size, processing counts, and status
    """
    queue_service = get_transcription_queue_service()
    stats = queue_service.get_stats()
    
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
    task = queue_service.get_task(task_id)
    
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found"
        )
    
    return TaskStatusResponse(
        task_id=task.task_id,
        status=task.status.value,
        filename=task.filename,
        created_at=task.created_at,
        started_processing_at=task.started_processing_at,
        completed_at=task.completed_at,
        result=task.result,
        error=task.error,
    )


@router.get("/queue/pending")
async def get_pending_tasks():
    """
    Get list of pending and processing tasks.
    
    Returns:
        List of tasks that are waiting or being processed
    """
    queue_service = get_transcription_queue_service()
    return {
        "tasks": queue_service.get_pending_tasks(),
        "count": len(queue_service.get_pending_tasks()),
    }
