"""
Transcription Task Model

Defines the task model specifically for transcription processing.
This implements StreamTaskProtocol and can be used with RedisStreamService.
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from non_realtime_stt_service.models.stream_base import (
    BaseStreamTask,
    parse_priority,
    TaskPriority,
    StreamTaskStatus,
)


@dataclass
class TranscriptionStreamTask(BaseStreamTask):
    """
    Transcription task model for Redis Stream.
    
    Inherits from BaseStreamTask (has task_id, message_id, priority, retry_count).
    Implements StreamTaskProtocol for audio/video transcription processing.
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
    
    # Processing tracking fields
    status: str = StreamTaskStatus.PENDING
    started_processing_at: Optional[float] = None
    completed_at: Optional[float] = None
    result: Optional[str] = None
    error: Optional[str] = None
    
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
            # Tracking fields
            "status": str(self.status),
            "started_processing_at": str(self.started_processing_at) if self.started_processing_at else "",
            "completed_at": str(self.completed_at) if self.completed_at else "",
            "result": self.result or "",
            "error": self.error or "",
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
        
        # Parse optional float fields
        started_processing_at = decoded.get("started_processing_at")
        started_processing_at = float(started_processing_at) if started_processing_at and started_processing_at != "" else None
        
        completed_at = decoded.get("completed_at")
        completed_at = float(completed_at) if completed_at and completed_at != "" else None
        
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
            # Tracking fields
            status=decoded.get("status", StreamTaskStatus.PENDING),
            started_processing_at=started_processing_at,
            completed_at=completed_at,
            result=decoded.get("result") or None,
            error=decoded.get("error") or None,
        )
