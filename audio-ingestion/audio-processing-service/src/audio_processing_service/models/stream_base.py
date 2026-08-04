"""
Stream Base Types for Redis Producer and Consumer

Common base types for both producing and consuming tasks from Redis Streams.

Copied verbatim from stt_service/models/stream_base.py (audio-ingestion
PLAN.md D28 point 3 -- reusing the existing Redis Stream consumer mechanism
as-is; this file has zero service-specific imports so it copies cleanly).
"""

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Protocol, Union, runtime_checkable


class TaskPriority(int, Enum):
    """Task priority levels (lower = higher priority)."""
    URGENT = 1
    HIGH = 3
    NORMAL = 5
    LOW = 7
    BACKGROUND = 9


class StreamTaskStatus(str, Enum):
    """Task status in Redis Stream."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD_LETTER = "dead_letter"


@dataclass
class BaseStreamTask:
    """Base dataclass for all consumer stream tasks."""
    task_id: str
    message_id: str  # Redis stream message ID

    retry_count: int = 0
    priority: Union[int, TaskPriority] = TaskPriority.NORMAL

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "priority": str(int(self.priority)),
            "retry_count": str(self.retry_count),
        }


@dataclass
class BaseProducerTask:
    """Base dataclass for all producer tasks."""
    priority: Union[int, TaskPriority] = TaskPriority.NORMAL
    retry_count: int = 0

    task_id: str = field(default_factory=lambda: f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}")
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        priority_value = parse_priority(self.priority)

        return {
            "task_id": self.task_id,
            "priority": str(priority_value),
            "created_at": str(self.created_at),
            "retry_count": str(self.retry_count),
        }


@runtime_checkable
class ProducerTaskProtocol(Protocol):
    task_id: str
    retry_count: int
    priority: Union[int, TaskPriority]

    def to_dict(self) -> Dict[str, Any]:
        ...


@runtime_checkable
class StreamTaskProtocol(Protocol):
    task_id: str
    message_id: str
    retry_count: int
    priority: Union[int, TaskPriority]

    def to_dict(self) -> Dict[str, Any]:
        ...

    @classmethod
    def from_stream_message(
        cls,
        message_id: str,
        data: Dict[bytes, bytes]
    ) -> 'StreamTaskProtocol':
        ...


def parse_priority(value: Union[int, TaskPriority, str]) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, TaskPriority):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            if "." in value:
                priority_name = value.split(".")[-1]
                try:
                    return int(TaskPriority[priority_name])
                except KeyError:
                    pass
    return TaskPriority.NORMAL
