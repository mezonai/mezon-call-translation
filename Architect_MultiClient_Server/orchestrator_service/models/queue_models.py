from pydantic import BaseModel, Field


# ========================================
# Base Models
# ========================================


class QueueStatsBase(BaseModel):                                # type: ignore[explicit-any]
    """Base model for queue statistics fields shared across multiple models."""

    stream_length: int = Field(default=0, description="Current queue length")
    total_enqueued: int = Field(default=0, description="Total tasks enqueued")
    total_processed: int = Field(default=0, description="Total tasks processed")
    total_failed: int = Field(default=0, description="Total tasks failed")
    active_workers: int = Field(default=0, description="Number of active workers")


class DLQRetryBase(BaseModel):                                  # type: ignore[explicit-any]
    """Base model for DLQ retry operation results."""

    queue_name: str
    success_count: int = Field(..., description="Number of successfully retried tasks")
    failed_count: int = Field(..., description="Number of tasks that failed to retry")
    total: int = Field(..., description="Total tasks processed")


# ========================================
# Queue Stats & Info Models
# ========================================


class ProducerQueueStats(QueueStatsBase):                       # type: ignore[explicit-any]
    """Response model for Redis producer queue statistics."""

    stream_key: str | None = Field(default=None, description="Redis stream key")
    error: str | None = Field(default=None, description="Error message if any")


class QueueInfo(QueueStatsBase):                                # type: ignore[explicit-any]
    """Model representing queue information discovered from Redis."""

    queue_name: str = Field(..., description="Queue identifier")
    stream_key: str = Field(..., description="Redis stream key")
    exists: bool = Field(..., description="Whether the queue exists")


class QueueStatsResponse(QueueStatsBase):                       # type: ignore[explicit-any]
    """Response model for queue statistics."""

    queue_name: str | None = Field(default=None, description="Queue identifier")
    stream_key: str | None = Field(default=None, description="Redis stream key")
    pending_count: int = Field(default=0, description="Number of pending tasks")
    error: str | None = Field(default=None, description="Error message if any")


class QueueInfoResponse(BaseModel):                             # type: ignore[explicit-any]
    """Response model for basic queue information."""

    queue_name: str
    stream_key: str
    stream_length: int
    active_workers: int
    exists: bool


class QueueListResponse(BaseModel):                             # type: ignore[explicit-any]
    """Response model for list of queues."""

    queues: list[QueueInfoResponse]
    count: int


# ========================================
# Task Models
# ========================================


class TaskStatusResponse(BaseModel):                            # type: ignore[explicit-any]
    """Response model for task status."""

    task_id: str
    status: str
    filename: str
    created_at: float
    started_processing_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    error: str | None = None


# ========================================
# Dead Letter Queue (DLQ) Models
# ========================================


class DLQTaskResponse(BaseModel):                               # type: ignore[explicit-any]
    """Response model for DLQ task information."""

    message_id: str = Field(..., description="Redis stream message ID")
    task_id: str = Field(..., description="Task identifier")
    filename: str | None = Field(None, description="Task filename (if applicable)")
    created_at: float = Field(..., description="Timestamp when task was created")
    dead_letter_at: float = Field(..., description="Timestamp when task was moved to DLQ")
    final_error: str = Field(..., description="Error message that caused task to fail")
    retry_count: int = Field(..., description="Number of retries attempted")
    status: str = Field(default="dead_letter", description="Task status")


class DLQListResponse(BaseModel):                               # type: ignore[explicit-any]
    """Response model for DLQ task list."""

    queue_name: str = Field(..., description="Queue identifier")
    dlq_stream_key: str = Field(..., description="Redis DLQ stream key")
    tasks: list[DLQTaskResponse]
    count: int = Field(..., description="Number of tasks in DLQ")


class DLQRetryResponse(DLQRetryBase):                           # type: ignore[explicit-any]
    """Response model for DLQ retry operation."""

    retried_tasks: list[str] = Field(..., description="List of task IDs that were retried")


class DLQRetryAllResponse(DLQRetryBase):                        # type: ignore[explicit-any]
    """Response model for bulk DLQ retry operation."""

    message: str = Field(..., description="Summary message")
