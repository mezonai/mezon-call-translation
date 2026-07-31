"""
Audio Derivative Task Model for Producer

Task model for enqueueing "transcode raw capture -> client-playable
derivative" jobs to Redis Stream (audio-ingestion/PLAN.md D17). Mirrors
transcription_task.py's shape/pattern -- consumed by audio-processing-service
(Phase 5), not by anything inside this repo yet.
"""

from dataclasses import dataclass
from typing import Any, Dict

from orchestrator_service.models.stream_base import BaseProducerTask


@dataclass
class AudioDerivativeTask(BaseProducerTask):
    """
    Task for audio-derivative transcode jobs sent to Redis Stream.

    Inherits from BaseProducerTask (has priority, retry_count, task_id, created_at).
    Implements ProducerTaskProtocol for use with RedisProducerService.
    """

    track_id: str = ""      # tracks.id (== record-service's recording_id, room_id:track_id)
    room_id: str = ""       # rooms.id (UUID, already resolved -- not the room name)
    bucket: str = ""
    object_key: str = ""    # raw PCM source object to transcode

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for Redis XADD. All values are strings as required by Redis."""
        data = super().to_dict()
        data.update({
            "track_id": self.track_id,
            "room_id": self.room_id,
            "bucket": self.bucket,
            "object_key": self.object_key,
        })
        return data
