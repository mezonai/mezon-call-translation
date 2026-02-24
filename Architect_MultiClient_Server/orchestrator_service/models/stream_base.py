"""
Stream Base Types for Redis Producer

Common base types for producing tasks to Redis Streams.
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
class BaseProducerTask:
    """
    Base dataclass for all producer tasks.
    
    Provides common fields required by Redis Stream protocol.
    Inherit from this class for specific task implementations.
    
    Example:
        @dataclass
        class MyTask(BaseProducerTask):
            my_field: str
            
            def to_dict(self) -> Dict[str, Any]:
                base_dict = super().to_dict()
                base_dict["my_field"] = self.my_field
                return base_dict
    """
    # Common required fields
    priority: Union[int, TaskPriority] = TaskPriority.NORMAL
    retry_count: int = 0
    
    # Auto-generated fields
    task_id: str = field(default_factory=lambda: f"task_{int(time.time() * 1000)}_{uuid.uuid4().hex[:4]}")
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict for Redis XADD.
        
        Override this in subclasses to add specific fields.
        """
        priority_value = parse_priority(self.priority)
        
        return {
            "task_id": self.task_id,
            "priority": str(priority_value),
            "created_at": str(self.created_at),
            "retry_count": str(self.retry_count),
        }


@runtime_checkable
class ProducerTaskProtocol(Protocol):
    """
    Protocol for tasks that can be produced to Redis Stream.
    
    Minimal interface required for enqueueing tasks.
    
    Required attributes:
        task_id: Unique identifier for the task
        retry_count: Number of retry attempts
        priority: Task priority level
    
    Required methods:
        to_dict(): Convert task to dict for Redis storage
    """
    
    task_id: str
    retry_count: int
    priority: Union[int, TaskPriority]
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert task to dictionary for Redis XADD.
        
        All values should be strings (Redis requirement).
        
        Returns:
            Dict with string keys and string values
        """
        ...


def parse_priority(value: Union[int, TaskPriority, str]) -> int:
    """
    Parse priority from various formats.
    
    Args:
        value: Priority as int, TaskPriority enum, or string
    
    Returns:
        Integer priority value
    """
    if isinstance(value, int):
        return value
    if isinstance(value, TaskPriority):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            # Handle format like "TaskPriority.NORMAL"
            if "." in value:
                priority_name = value.split(".")[-1]
                try:
                    return int(TaskPriority[priority_name])
                except KeyError:
                    pass
    return TaskPriority.NORMAL
