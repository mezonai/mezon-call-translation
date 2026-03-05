"""
Transcription API Controller

Provides REST endpoints for transcription queue management.
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from typing import Optional
from bson import ObjectId

from ..service.transcription_queue_service import (
    get_transcription_queue_service,
    TranscriptionTask,
)

from stt_service.service.mongodb_service import get_mongodb_service


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


class TranscriptionRequest(BaseModel):
    """Request model for queueing a transcription task (simplified structure)."""
    egressId: str
    filename: str
    location: str
    duration: str
    startedAt: str
    endedAt: str
    source: Optional[str] = None

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
        
        # Create transcription task with all necessary info
        # Track metadata will be updated in the processor when task starts processing
        task = TranscriptionTask(
            filename=request.filename,
            started_at=request.startedAt,
            ended_at=request.endedAt,
            duration=request.duration,
            location=request.location,
            egress_id=request.egressId,
            source=request.source,
        )
        
        # Enqueue (non-blocking)
        task_id = None
        try:
            task_id = queue_service.enqueue_nowait(task)
            logger.info(f"Task {task_id} enqueued immediately")
        except asyncio.QueueFull:
            # Queue full, use blocking version with 5s timeout
            logger.warning(f"Queue full, trying blocking enqueue with timeout for {task.filename}")
            try:
                task_id = await queue_service.enqueue(task, timeout=5.0)
                logger.info(f"Task {task_id} enqueued after waiting")
            except asyncio.TimeoutError:
                logger.error(f"Failed to enqueue {task.filename}: Queue full and timeout after 5s")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Transcription queue is full. Please try again later."
                )
        
        stats = queue_service.get_stats()
        
        return TranscriptionResponse(
            success=True,
            task_id=task_id,
            message="Task queued successfully",
            queue_size=stats["queue_size"],
        )
    
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to queue transcription task: {e}", exc_info=True)
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
