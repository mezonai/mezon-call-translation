"""
Generic Queue Monitoring API

Provides endpoints for monitoring any queue type (transcription, TTS, etc.).
Automatically discovers available queues from Redis.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, Field

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import QUEUES_VIEW_STATS
from orchestrator_service.services.queue_discovery import QueueDiscovery
from orchestrator_service.services.queue_service import get_queue_service_by_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


class TaskStatusResponse(BaseModel):
    """Response model for task status."""

    task_id: str
    status: str
    filename: str
    created_at: float
    started_processing_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    error: str | None = None


class QueueStatsResponse(BaseModel):
    """Response model for queue statistics."""

    queue_name: str | None = Field(None, description="Queue identifier")
    stream_key: str | None = Field(None, description="Redis stream key")
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

    queues: list[QueueInfoResponse]
    count: int


class DLQTaskResponse(BaseModel):
    """Response model for DLQ task information."""

    message_id: str = Field(..., description="Redis stream message ID")
    task_id: str = Field(..., description="Task identifier")
    filename: str | None = Field(None, description="Task filename (if applicable)")
    created_at: float = Field(..., description="Timestamp when task was created")
    dead_letter_at: float = Field(..., description="Timestamp when task was moved to DLQ")
    final_error: str = Field(..., description="Error message that caused task to fail")
    retry_count: int = Field(..., description="Number of retries attempted")
    status: str = Field(default="dead_letter", description="Task status")


class DLQListResponse(BaseModel):
    """Response model for DLQ task list."""

    queue_name: str = Field(..., description="Queue identifier")
    dlq_stream_key: str = Field(..., description="Redis DLQ stream key")
    tasks: list[DLQTaskResponse]
    count: int = Field(..., description="Number of tasks in DLQ")


class DLQRetryResponse(BaseModel):
    """Response model for DLQ retry operation."""

    queue_name: str
    retried_tasks: list[str] = Field(..., description="List of task IDs that were retried")
    success_count: int
    failed_count: int
    total: int


class DLQRetryAllResponse(BaseModel):
    """Response model for bulk DLQ retry operation."""

    queue_name: str
    success_count: int = Field(..., description="Number of successfully retried tasks")
    failed_count: int = Field(..., description="Number of tasks that failed to retry")
    total: int = Field(..., description="Total tasks processed")
    message: str = Field(..., description="Summary message")


# ========================================
# Generic Queue Endpoints
# ========================================


@router.get("/list", response_model=QueueListResponse)
async def list_available_queues(auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS))):
    """
    List all available queues discovered from Redis.

    Automatically scans Redis for existing streams and returns
    their basic information. No manual registration required.

    Returns:
        List of available queues with their current state
    """
    try:
        queues_data = await QueueDiscovery.list_queues()

        queues = [QueueInfoResponse(**queue_data) for queue_data in queues_data]

        return QueueListResponse(queues=queues, count=len(queues))

    except Exception as e:
        logger.error(f"Error listing queues: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to list queues: {e!s}") from e


@router.get("/{queue_name}/stats", response_model=QueueStatsResponse)
async def get_queue_stats_by_name(
    queue_name: str = Path(..., description="Queue identifier (e.g., transcription, tts)"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error getting stats for queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get queue stats: {e!s}"
        ) from e


@router.get("/{queue_name}/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    task_id: str = Path(..., description="Task ID"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
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
                status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found in queue '{queue_name}'"
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting task {task_id} from queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get task status: {e!s}"
        ) from e


@router.get("/{queue_name}/pending")
async def get_pending_tasks_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error getting pending tasks for queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get pending tasks: {e!s}"
        ) from e


# ========================================
# Queue Overview Endpoint
# ========================================


@router.get("/overview")
async def get_all_queues_overview(auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS))):
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
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get queue overview: {e!s}"
        ) from e


# ========================================
# Dead Letter Queue (DLQ) Endpoints
# ========================================


@router.get("/{queue_name}/dlq", response_model=DLQListResponse)
async def get_dlq_tasks(
    queue_name: str = Path(..., description="Queue identifier"),
    limit: int = 100,
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
):
    """
    Get list of tasks in Dead Letter Queue.

    Retrieves all tasks that have exceeded max_retries (default: 3)
    and failed permanently. Includes error information and timestamps.

    Args:
        queue_name: Queue identifier (transcription, tts, etc.)
        limit: Maximum number of DLQ tasks to return (default: 100)

    Returns:
        List of DLQ tasks with error details

    Raises:
        HTTPException: If queue not found or database error
    """
    try:
        queue_service = get_queue_service_by_name(queue_name)
        dlq_tasks = await queue_service.get_dlq_tasks(limit=limit)

        return DLQListResponse(
            queue_name=queue_name,
            dlq_stream_key=f"{queue_service.stream_key}:dlq",
            tasks=[DLQTaskResponse(**task) for task in dlq_tasks],
            count=len(dlq_tasks),
        )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error getting DLQ tasks for queue '{queue_name}': {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to get DLQ tasks: {e!s}") from e


@router.post("/{queue_name}/dlq/retry", response_model=DLQRetryAllResponse)
async def retry_dlq_tasks(
    queue_name: str = Path(..., description="Queue identifier"),
    task_id: str = Path(..., description="Task ID to retry a single task "),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
):
    """
    Retry tasks from Dead Letter Queue.

    """
    try:
        queue_service = get_queue_service_by_name(queue_name)

        # Retry single task
        success = await queue_service.retry_dlq_task(task_id)

        if success:
            return DLQRetryAllResponse(
                queue_name=queue_name,
                success_count=1,
                failed_count=0,
                total=1,
                message=f"Successfully retried task {task_id}",
            )
        else:
            return DLQRetryAllResponse(
                queue_name=queue_name,
                success_count=0,
                failed_count=1,
                total=1,
                message=f"Failed to retry task {task_id} (not found or error)",
            )

    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Error retrying DLQ tasks for queue '{queue_name}': {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Failed to retry DLQ tasks: {e!s}"
        ) from e
