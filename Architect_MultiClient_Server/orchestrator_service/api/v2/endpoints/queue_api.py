"""
Generic Queue Monitoring API

Provides endpoints for monitoring any queue type (transcription, TTS, etc.).
Automatically discovers available queues from Redis.
"""

import logging
from fastapi import APIRouter, HTTPException, status, Path, Depends
from pydantic import BaseModel, Field
from typing import Optional, List

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.services.queue_service import (
    get_queue_service_by_name
)
from orchestrator_service.services.queue_discovery import QueueDiscovery

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


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
    queue_name: Optional[str] = Field(None, description="Queue identifier")
    stream_key: Optional[str] = Field(None, description="Redis stream key")
    stream_length: int = Field(..., description="Current queue length")
    total_enqueued: int = Field(..., description="Total tasks enqueued")
    total_processed: int = Field(..., description="Total tasks processed")
    total_failed: int = Field(..., description="Total tasks failed")
    pending_count: int = Field(..., description="Number of pending tasks")
    active_workers: int = Field(..., description="Number of active workers")


class QueueInfoResponse(BaseModel):
    """Response model for basic queue information."""
    queue_name: str
    stream_key: str
    stream_length: int
    active_workers: int
    exists: bool


class QueueListResponse(BaseModel):
    """Response model for list of queues."""
    queues: List[QueueInfoResponse]
    count: int


# ========================================
# Generic Queue Endpoints
# ========================================

@router.get("/list", response_model=QueueListResponse)
async def list_available_queues(auth: AuthContext = Depends(require_any_permission("queues:view_stats"))):
    """
    List all available queues discovered from Redis.
    
    Automatically scans Redis for existing streams and returns
    their basic information. No manual registration required.
    
    Returns:
        List of available queues with their current state
    """
    try:
        queues_data = await QueueDiscovery.list_queues()
        
        queues = [
            QueueInfoResponse(**queue_data)
            for queue_data in queues_data
        ]
        
        return QueueListResponse(
            queues=queues,
            count=len(queues)
        )
    
    except Exception as e:
        logger.error(f"Error listing queues: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list queues: {str(e)}"
        )


@router.get("/{queue_name}/stats", response_model=QueueStatsResponse)
async def get_queue_stats_by_name(
    queue_name: str = Path(..., description="Queue identifier (e.g., transcription, tts)"),
    auth: AuthContext = Depends(require_any_permission("queues:view_stats"))
):
    """
    Get statistics for a specific queue.
    
    Args:
        queue_name: Queue identifier (transcription, tts, agent, etc.)
    
    Returns:
        Queue statistics including size, processing counts, and workers
    
    Raises:
        HTTPException: If queue is not found or disabled
    """
    try:
        queue_service = get_queue_service_by_name(queue_name)
        stats = await queue_service.get_stats()
        return QueueStatsResponse(**stats)
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error getting stats for queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get queue stats: {str(e)}"
        )


@router.get("/{queue_name}/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    task_id: str = Path(..., description="Task ID"),
    auth: AuthContext = Depends(require_any_permission("queues:view_stats"))
):
    """
    Get status of a specific task in a queue.
    
    Args:
        queue_name: Queue identifier
        task_id: The task ID returned when queuing
        
    Returns:
        Task status and result if completed
    
    Raises:
        HTTPException: If queue or task is not found
    """
    try:
        queue_service = get_queue_service_by_name(queue_name)
        task_data = await queue_service.get_task(task_id)
        
        if not task_data:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found in queue '{queue_name}'"
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
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task {task_id} from queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get task status: {str(e)}"
        )


@router.get("/{queue_name}/pending")
async def get_pending_tasks_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    auth: AuthContext = Depends(require_any_permission("queues:view_stats"))
):
    """
    Get list of pending tasks in a specific queue.
    
    Args:
        queue_name: Queue identifier
    
    Returns:
        List of tasks that are waiting or being processed
    
    Raises:
        HTTPException: If queue is not found or disabled
    """
    try:
        queue_service = get_queue_service_by_name(queue_name)
        pending_tasks = await queue_service.get_pending_tasks()
        
        return {
            "queue_name": queue_name,
            "tasks": pending_tasks,
            "count": len(pending_tasks),
        }
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Error getting pending tasks for queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get pending tasks: {str(e)}"
        )


# ========================================
# Queue Overview Endpoint
# ========================================

@router.get("/overview")
async def get_all_queues_overview(auth: AuthContext = Depends(require_any_permission("queues:view_stats"))):
    """
    Get overview of all queues discovered from Redis.
    
    Returns statistics for all existing queues in one call.
    Useful for dashboard displays.
    
    Returns:
        Dictionary with queue names as keys and their stats as values
    """
    try:
        # Discover all queues from Redis
        queues_data = await QueueDiscovery.list_queues()
        overview = {}
        
        for queue_data in queues_data:
            queue_name = queue_data["queue_name"]
            try:
                queue_service = get_queue_service_by_name(queue_name)
                stats = await queue_service.get_stats()
                overview[queue_name] = stats
            except Exception as e:
                logger.error(f"Error getting stats for queue '{queue_name}': {e}")
                overview[queue_name] = {
                    "error": str(e),
                    "queue_name": queue_name,
                }
        
        return {
            "queues": overview,
            "count": len(queues_data),
            "timestamp": __import__("time").time(),
        }
    
    except Exception as e:
        logger.error(f"Error getting queue overview: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get queue overview: {str(e)}"
        )
