"""
Audio Derivative Task Model for Producer

Task model for enqueueing "transcode raw capture -> client-playable
derivative" jobs to Redis Stream (audio-ingestion/PLAN.md D17). Mirrors
transcription_task.py's shape/pattern -- consumed by audio-processing-service
(Phase 5), not by anything inside this repo yet.
"""

from dataclasses import dataclass

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
    # Raw capture's actual format (record-service's RecordingEventRequest.sample_rate/
    # channels -- see HttpEventReporter._to_payload). Previously not threaded
    # through here at all; audio-processing-service just hardcoded 16000/1 for
    # every track (audio-ingestion PLAN.md D28 point 1), which broke once a
    # second real capture rate (the agent's own 24kHz TTS track, D3x) existed.
    sample_rate: int = 16000
    channels: int = 1

    def to_dict(self) -> dict[str, str]:
        """Convert to dict for Redis XADD. All values are strings as required by Redis."""
        data = super().to_dict()
        data.update({
            "track_id": self.track_id,
            "room_id": self.room_id,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "sample_rate": str(self.sample_rate),
            "channels": str(self.channels),
        })
        return data
