"""
Audio Derivative Stream Task Model (consumer side)

Parses tasks enqueued by orchestrator_service's AudioDerivativeService onto
the `audio_derivative:stream` Redis Stream (audio-ingestion PLAN.md D17).
Field names/wire format must match the producer side exactly:
Architect_MultiClient_Server/orchestrator_service/models/audio_derivative_task.py
(AudioDerivativeTask.to_dict()).
"""

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from audio_processing_service.models.stream_base import (
    BaseStreamTask,
    parse_priority,
    TaskPriority,
    StreamTaskStatus,
)


@dataclass
class AudioDerivativeStreamTask(BaseStreamTask):
    """Consumer-side task for "transcode raw capture -> derivative" jobs."""

    track_id: str = ""      # tracks.id (== record-service's recording_id)
    room_id: str = ""       # rooms.id (UUID)
    bucket: str = ""
    object_key: str = ""    # raw PCM source object to transcode
    created_at: float = field(default_factory=time.time)

    # Local processing-tracking fields, not part of the producer's wire format.
    status: str = StreamTaskStatus.PENDING
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        data = super().to_dict()
        data.update({
            "track_id": self.track_id,
            "room_id": self.room_id,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "created_at": str(self.created_at),
        })
        return data

    @classmethod
    def from_stream_message(
        cls,
        message_id: str,
        data: Dict[bytes, bytes]
    ) -> 'AudioDerivativeStreamTask':
        decoded = {k.decode(): v.decode() for k, v in data.items()}

        return cls(
            task_id=decoded.get("task_id", ""),
            message_id=message_id,
            retry_count=int(decoded.get("retry_count", 0)),
            priority=parse_priority(decoded.get("priority", TaskPriority.NORMAL)),
            track_id=decoded.get("track_id", ""),
            room_id=decoded.get("room_id", ""),
            bucket=decoded.get("bucket", ""),
            object_key=decoded.get("object_key", ""),
            created_at=float(decoded.get("created_at", time.time())),
        )
