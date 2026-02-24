"""
Transcription Task Model

Defines the task model specifically for transcription processing.
This implements StreamTaskProtocol and can be used with RedisStreamService.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from stt_service.models.stream_base import (
    BaseStreamTask,
    parse_priority,
    TaskPriority,
)


@dataclass
class TranscriptionStreamTask(BaseStreamTask):
    """
    Transcription task model for Redis Stream.
    
    Inherits from BaseStreamTask (has task_id, message_id, priority, retry_count).
    Implements StreamTaskProtocol for audio/video transcription processing.
    
    Example:
        from stt_service.service.redis_stream_service import create_stream_service
        from stt_service.models import TranscriptionStreamTask
        
        service = create_stream_service(TranscriptionStreamTask)
        tasks = await service.read_tasks()
    """
    
    # Transcription-specific fields
    filename: str = ""
    egress_id: str = ""
    started_at: str = ""
    ended_at: str = ""
    duration: str = ""
    location: str = ""
    source: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for Redis storage."""
        # Get base fields from parent
        data = super().to_dict()
        
        # Add transcription-specific fields
        data.update({
            "filename": self.filename,
            "egress_id": self.egress_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration": self.duration,
            "location": self.location,
            "source": self.source or "",
            "created_at": str(self.created_at),
        })
        
        return data
    
    @classmethod
    def from_stream_message(
        cls, 
        message_id: str, 
        data: Dict[bytes, bytes]
    ) -> 'TranscriptionStreamTask':
        """Create TranscriptionStreamTask from Redis stream message."""
        # Decode bytes to strings
        decoded = {k.decode(): v.decode() for k, v in data.items()}
        
        return cls(
            task_id=decoded.get("task_id", ""),
            message_id=message_id,
            retry_count=int(decoded.get("retry_count", 0)),
            priority=parse_priority(decoded.get("priority", TaskPriority.NORMAL)),
            filename=decoded.get("filename", ""),
            egress_id=decoded.get("egress_id", ""),
            started_at=decoded.get("started_at", ""),
            ended_at=decoded.get("ended_at", ""),
            duration=decoded.get("duration", ""),
            location=decoded.get("location", ""),
            source=decoded.get("source") or None,
            created_at=float(decoded.get("created_at", time.time())),
        )
