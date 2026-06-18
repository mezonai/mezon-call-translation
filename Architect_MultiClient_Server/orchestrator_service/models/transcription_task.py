"""
Transcription Task Model for Producer

Task model for enqueueing transcription jobs to Redis Stream.
"""

from dataclasses import dataclass
from typing import Any, Dict

from orchestrator_service.models.stream_base import BaseProducerTask


@dataclass
class TranscriptionTask(BaseProducerTask):
    """
    Task for transcription jobs sent to Redis Stream.

    Inherits from BaseProducerTask (has priority, retry_count, task_id, created_at).
    Implements ProducerTaskProtocol for use with RedisProducerService.
    """

    # Required fields for transcription
    egress_id: str = ""
    filename: str = ""
    location: str = ""
    duration: str = ""
    started_at: str = ""
    ended_at: str = ""

    # Optional fields
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to dict for Redis XADD.

        All values are strings as required by Redis.
        """
        # Get base fields from parent
        data = super().to_dict()

        # Add transcription-specific fields
        data.update(
            {
                "filename": self.filename,
                "egress_id": self.egress_id,
                "started_at": self.started_at,
                "ended_at": self.ended_at,
                "duration": self.duration,
                "location": self.location,
                "source": self.source or "",
            }
        )

        return data
