"""
SaveTranscriptionTask Model - For progressive saving via Redis

This task represents a chunk of transcription segments to be saved to MongoDB.
Multiple tasks are sent for a single transcription (batched approach).
"""

import json
from dataclasses import dataclass, field
from typing import Any

from .stream_base import BaseProducerTask, TaskPriority, parse_priority


@dataclass
class SaveTranscriptionTask(BaseProducerTask):
    """
    Task for saving a batch of transcription segments to MongoDB.

    Multiple instances of this task are created for a single audio transcription,
    each containing a chunk of segments. The last chunk has is_final=True.

    Attributes:
        track_ref_id: Egress ID / Track reference ID
        segments: List of segment dicts with start, end, text, confidence
        chunk_index: Sequential index of this chunk (0, 1, 2, ...)
        start_time: Start time of first segment in this chunk (seconds)
        end_time: End time of last segment in this chunk (seconds)
        item_count: Number of segments in this chunk
        is_final: True if this is the last chunk for the transcription
        status: Processing status (pending, processing, completed, failed)
        message_id: Redis stream message ID (set when consumed from Redis)
    """

    # Required fields (kw_only allows them after parent's default fields)
    track_ref_id: str = field(kw_only=True)
    segments: list[dict[str, Any]] = field(kw_only=True)
    chunk_index: int = field(kw_only=True)
    start_time: float = field(kw_only=True)
    end_time: float = field(kw_only=True)
    item_count: int = field(kw_only=True)
    is_final: bool = field(kw_only=True)

    # Optional fields with defaults
    status: str = "pending"

    # Redis stream metadata (set when consumed from Redis)
    message_id: str = ""  # Redis stream message ID (e.g., "1234567890-0")

    def to_dict(self) -> dict[str, Any]:
        """
        Convert task to dict for Redis XADD.

        Segments are serialized as JSON string to store in Redis.
        """
        base_dict = super().to_dict()

        # Serialize segments as JSON string
        segments_json = json.dumps(self.segments)

        base_dict.update(
            {
                "track_ref_id": self.track_ref_id,
                "segments": segments_json,
                "chunk_index": str(self.chunk_index),
                "start_time": str(self.start_time),
                "end_time": str(self.end_time),
                "item_count": str(self.item_count),
                "is_final": str(self.is_final),
                "status": self.status,
            }
        )

        return base_dict

    @classmethod
    def from_stream_message(cls, message_id: str, data: dict[bytes, bytes]) -> "SaveTranscriptionTask":
        """
        Parse SaveTranscriptionTask from Redis stream message.

        Args:
            message_id: Redis stream message ID
            data: Raw bytes data from Redis stream

        Returns:
            SaveTranscriptionTask instance
        """
        # Decode bytes to strings
        decoded = {
            k.decode() if isinstance(k, bytes) else k: v.decode() if isinstance(v, bytes) else v
            for k, v in data.items()
        }

        # Parse segments from JSON
        segments_json = decoded.get("segments", "[]")
        segments = json.loads(segments_json)

        # Parse boolean
        is_final = decoded.get("is_final", "False").lower() in ("true", "1")

        return cls(
            task_id=decoded.get("task_id", ""),
            message_id=message_id,  # Set Redis stream message ID
            track_ref_id=decoded.get("track_ref_id", ""),
            segments=segments,
            chunk_index=int(decoded.get("chunk_index", 0)),
            start_time=float(decoded.get("start_time", 0.0)),
            end_time=float(decoded.get("end_time", 0.0)),
            item_count=int(decoded.get("item_count", 0)),
            is_final=is_final,
            status=decoded.get("status", "pending"),
            priority=parse_priority(decoded.get("priority", TaskPriority.NORMAL)),
            retry_count=int(decoded.get("retry_count", 0)),
            created_at=float(decoded.get("created_at", 0.0)),
        )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"SaveTranscriptionTask("
            f"track_ref_id={self.track_ref_id}, "
            f"chunk={self.chunk_index}, "
            f"items={self.item_count}, "
            f"time={self.start_time:.1f}-{self.end_time:.1f}s, "
            f"final={self.is_final})"
        )
