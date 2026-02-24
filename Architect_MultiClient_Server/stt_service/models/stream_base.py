"""
Stream Base Types for Redis Consumer

Common base types for consuming tasks from Redis Streams.
"""

from dataclasses import dataclass
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
    """
    Base dataclass for all consumer stream tasks.
    
    Provides common fields required by Redis Stream protocol.
    Inherit from this class for specific task implementations.
    """
    # Required by StreamTaskProtocol
    task_id: str
    message_id: str  # Redis stream message ID
    
    # Common stream fields
    retry_count: int = 0
    priority: Union[int, TaskPriority] = TaskPriority.NORMAL
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dictionary for Redis storage.
        
        Override this in subclasses to add specific fields.
        """
        return {
            "task_id": self.task_id,
            "priority": str(int(self.priority)),
            "retry_count": str(self.retry_count),
        }


@runtime_checkable
class StreamTaskProtocol(Protocol):
    """
    Protocol defining the interface that ALL task models must implement.
    
    Any class with these attributes/methods is considered compatible
    (structural typing - no explicit inheritance required).
    
    Required attributes:
        task_id: Unique identifier for the task
        message_id: Redis stream message ID (e.g., "1234567890-0")
        retry_count: Number of retry attempts
        priority: Task priority level
    
    Required methods:
        to_dict(): Convert task to dict for Redis storage
        from_stream_message(): Class method to parse task from Redis stream message
    """
    
    task_id: str
    message_id: str
    retry_count: int
    priority: Union[int, TaskPriority]
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert task to dictionary for Redis storage.
        
        All values should be strings (Redis requirement for XADD).
        
        Returns:
            Dict with string keys, suitable for Redis XADD
        """
        ...
    
    @classmethod
    def from_stream_message(
        cls, 
        message_id: str, 
        data: Dict[bytes, bytes]
    ) -> 'StreamTaskProtocol':
        """
        Create task instance from Redis stream message.
        
        Args:
            message_id: Redis stream message ID (e.g., "1234567890-0")
            data: Raw data from Redis stream (bytes keys & values)
        
        Returns:
            New instance of the task class
        """
        ...


def parse_priority(value: Union[int, TaskPriority, str]) -> int:
    """
    Parse priority from Redis - handles both int strings and old enum string format.
    
    Args:
        value: Priority value (int, TaskPriority, or string)
    
    Returns:
        Integer priority value
    """
    if isinstance(value, int):
        return value
    if isinstance(value, TaskPriority):
        return int(value)
    if isinstance(value, str):
        # Try direct int conversion first
        try:
            return int(value)
        except ValueError:
            # Handle old format like "TaskPriority.NORMAL"
            if "." in value:
                priority_name = value.split(".")[-1]
                try:
                    return int(TaskPriority[priority_name])
                except KeyError:
                    pass
    # Default to NORMAL
    return TaskPriority.NORMAL
