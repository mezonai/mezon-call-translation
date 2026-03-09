"""
SaveTranscriptionTask Model - For progressive saving via Redis

This task represents a chunk of transcription segments to be saved to MongoDB.
Multiple tasks are sent for a single transcription (batched approach).
"""

import json
from dataclasses import dataclass, field
from typing import Dict, List, Any

from .stream_base import BaseProducerTask


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
    segments: List[Dict[str, Any]] = field(kw_only=True)
    chunk_index: int = field(kw_only=True)
    start_time: float = field(kw_only=True)
    end_time: float = field(kw_only=True)
    item_count: int = field(kw_only=True)
    is_final: bool = field(kw_only=True)
    
    # Optional fields with defaults
    status: str = "pending"
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convert task to dict for Redis XADD.
        
        Segments are serialized as JSON string to store in Redis.
        """
        base_dict = super().to_dict()
        
        # Serialize segments as JSON string
        segments_json = json.dumps(self.segments)
        
        base_dict.update({
            "track_ref_id": self.track_ref_id,
            "segments": segments_json,
            "chunk_index": str(self.chunk_index),
            "start_time": str(self.start_time),
            "end_time": str(self.end_time),
            "item_count": str(self.item_count),
            "is_final": str(self.is_final),
            "status": self.status,
        })
        
        return base_dict
    
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
