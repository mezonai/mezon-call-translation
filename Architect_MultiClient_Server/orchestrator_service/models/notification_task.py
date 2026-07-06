"""
Notification Task Model for Mezon Channel Notifications

Task model for enqueueing notifications to be sent to Mezon channels via webhooks.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from orchestrator_service.models.stream_base import BaseProducerTask, TaskPriority, parse_priority


@dataclass
class NotificationTask(BaseProducerTask):
    """
    Task for sending notifications to Mezon channels via webhooks.

    Inherits from BaseProducerTask (has priority, retry_count, task_id, created_at).
    """

    # Required fields
    title: str = field(kw_only=True)  # Brief title/subject
    message: dict[str, Any] = field(kw_only=True, default_factory=dict)  # Message content as dict

    # Redis stream metadata
    message_id: str = ""
    status: str = "pending"

    def to_dict(self) -> dict[str, str]:
        """
        Convert task to dict for Redis XADD.

        All values are strings as required by Redis.
        Message dict is serialized to JSON string.
        """
        # Get base fields from parent
        data = super().to_dict()

        # Add notification-specific fields
        data.update(
            {
                "title": self.title,
                "message": json.dumps(self.message),
                "status": self.status,
            }
        )

        return data

    @classmethod
    def from_stream_message(cls, message_id: str, data: dict[bytes, bytes]) -> "NotificationTask":
        """
        Parse NotificationTask from Redis stream message.

        Args:
            message_id: Redis stream message ID
            data: Raw bytes data from Redis stream

        Returns:
            NotificationTask instance
        """
        import json

        # Decode bytes to strings
        decoded = {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in data.items()
        }

        # Parse message dict from JSON
        message = json.loads(decoded.get("message", "{}"))

        task = cls(
            # BaseProducerTask fields
            priority=parse_priority(decoded.get("priority", TaskPriority.NORMAL)),
            retry_count=int(decoded.get("retry_count", "0")),
            task_id=decoded.get("task_id", ""),
            created_at=float(decoded.get("created_at", "0")),
            # NotificationTask fields
            title=decoded.get("title", ""),
            message=message,
            status=decoded.get("status", "pending"),
        )

        task.message_id = message_id
        return task
