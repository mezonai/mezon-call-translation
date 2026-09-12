"""
Generic Queue Monitoring API

Provides endpoints for monitoring any queue type (transcription, TTS, etc.).
Automatically discovers available queues from Redis.
"""

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Path, status

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import QUEUES_VIEW_STATS
from orchestrator_service.models.queue_models import (
    DLQListResponse,
    DLQRetryAllResponse,
    PendingTaskListResponse,
    QueueInfoResponse,
    QueueListResponse,
    QueueOverviewResponse,
    QueueStatsResponse,
    TaskStatusResponse,
)
from orchestrator_service.services.queue_discovery import QueueDiscovery
from orchestrator_service.services.queue_service import get_queue_service_by_name

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/queue", tags=["queue"])


# ========================================
# Generic Queue Endpoints
# ========================================


@router.get("/list", response_model=QueueListResponse)
async def list_available_queues(
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS))
) -> QueueListResponse:
    """
    List all available queues discovered from Redis.

    Automatically scans Redis for existing streams and returns
    their basic information. No manual registration required.

    Returns:
        List of available queues with their current state
    """
    queues_data = await QueueDiscovery.list_queues()

    queues = [
        QueueInfoResponse(**queue_data.model_dump())
        for queue_data in queues_data
    ]

    return QueueListResponse(queues=queues, count=len(queues))


@router.get("/{queue_name}/stats", response_model=QueueStatsResponse)
async def get_queue_stats_by_name(
    queue_name: str = Path(..., description="Queue identifier (e.g., transcription, tts)"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
) -> QueueStatsResponse:
    """
    Get statistics for a specific queue.

    Args:
        queue_name: Queue identifier (transcription, tts, agent, etc.)

    Returns:
        Queue statistics including size, processing counts, and workers

    Raises:
        HTTPException: If queue is not found or disabled
    """
    queue_service = get_queue_service_by_name(queue_name)
    return await queue_service.get_stats()


@router.get("/{queue_name}/task/{task_id}", response_model=TaskStatusResponse)
async def get_task_status_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    task_id: str = Path(..., description="Task ID"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
) -> TaskStatusResponse:
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



@router.get("/{queue_name}/pending", response_model=PendingTaskListResponse)
async def get_pending_tasks_by_queue(
    queue_name: str = Path(..., description="Queue identifier"),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
) -> PendingTaskListResponse:
    """
    Get list of pending tasks in a specific queue.

    Args:
        queue_name: Queue identifier

    Returns:
        List of tasks that are waiting or being processed

    Raises:
        HTTPException: If queue is not found or disabled
    """
    queue_service = get_queue_service_by_name(queue_name)
    pending_tasks = await queue_service.get_pending_tasks()

    return PendingTaskListResponse(
        queue_name=queue_name,
        tasks=pending_tasks,
        count=len(pending_tasks)
    )


# ========================================
# Queue Overview Endpoint
# ========================================


@router.get("/overview", response_model=QueueOverviewResponse)
async def get_all_queues_overview(
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS))
) -> QueueOverviewResponse:
    """
    Get overview of all queues discovered from Redis.

    Returns statistics for all existing queues in one call.
    Useful for dashboard displays.

    Returns:
        Dictionary with queue names as keys and their stats as values
    """
    overview, count = await QueueDiscovery.get_queues_overview()

    return QueueOverviewResponse(
        queues=overview,
        count=count,
        timestamp=time.time(),
    )


# ========================================
# Dead Letter Queue (DLQ) Endpoints
# ========================================


@router.get("/{queue_name}/dlq", response_model=DLQListResponse)
async def get_dlq_tasks(
    queue_name: str = Path(..., description="Queue identifier"),
    limit: int = 100,
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
) -> DLQListResponse:
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
    queue_service = get_queue_service_by_name(queue_name)
    dlq_tasks = await queue_service.get_dlq_tasks(limit=limit)

    return DLQListResponse(
        queue_name=queue_name,
        dlq_stream_key=f"{queue_service.stream_key}:dlq",
        tasks=dlq_tasks,
        count=len(dlq_tasks),
    )


@router.post("/{queue_name}/dlq/retry/{task_id}", response_model=DLQRetryAllResponse)
async def retry_dlq_tasks(
    queue_name: str = Path(..., description="Queue identifier"),
    task_id: str = Path(..., description="Task ID to retry a single task "),
    auth: AuthContext = Depends(require_any_permission(QUEUES_VIEW_STATS)),
) -> DLQRetryAllResponse:
    """
    Retry tasks from Dead Letter Queue.

    """
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
    return DLQRetryAllResponse(
        queue_name=queue_name,
        success_count=0,
        failed_count=1,
        total=1,
        message=f"Failed to retry task {task_id} (not found or error)",
    )
