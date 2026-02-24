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

from ..service.redis_transcription_queue_service import (
    get_transcription_queue_service,
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


@router.post("/tracks/metadata", response_model=dict)
async def save_track_metadata(request: TrackMetadataRequest):
    """
    Save track metadata using egress_id as _id.
    Creates the room if it doesn't exist, then inserts the track document.
    """
    try:
        mongodb_service = get_mongodb_service()
        if not mongodb_service.connected:
            await mongodb_service.connect()

        # Convert room_ref_id string to ObjectId
        try:
            room_ref_id = ObjectId(request.room_ref_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid room_ref_id: {request.room_ref_id}",
            )

        # Check if room exists
        room = await mongodb_service.get_room_by_id(room_ref_id)
        if not room:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Room not found: {request.room_ref_id}",
            )

        track_id_result = await mongodb_service.save_track_metadata(
            egress_id=request.egress_id,
            track_id=request.track_id,
            room_ref_id=room_ref_id,
            participant_identity=request.participant_identity,
            status="pending",
        )

        if not track_id_result:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to save track metadata",
            )

        return {
            "success": True,
            "message": "Track metadata saved successfully",
            "track_id": str(track_id_result),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save track metadata: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to save track metadata: {str(e)}",
        )


@router.post("/rooms/start",response_model=dict)
async def start_room_transcription(request: SessionInfo):
    mongodb_service = get_mongodb_service()
    try:
        room_id =  await mongodb_service.create_room_session(
            room_name=request.room_name
        )
        return {
            "success": True,
            "message": f"Room {request.room_name} started successfully",
            "room_id": str(room_id)
        }
    except Exception as e:
        logger.exception("Failed to start room transcription")
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )
    
@router.put("/rooms/end",response_model=dict)
async def end_room_transcription(request: RoomInfo):
    mongodb_service = get_mongodb_service()
    logger.info(f"Ending transcription for room: {request.name}")
    try:
        updated = await mongodb_service.final_room_status(
            room_name=request.name,
            room_id=request.room_id
        )

        if not updated:
            raise HTTPException(
                status_code=404,
                detail=f"Room {request.name} not found or already ended"
            )

        return {
            "success": True,
            "message": f"Room {request.name} ended successfully"
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Failed to end room transcription: %s", e)
        raise HTTPException(
            status_code=500,
            detail="Internal server error"
        )



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
