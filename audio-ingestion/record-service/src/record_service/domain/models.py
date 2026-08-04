"""Core domain models. Zero dependency on any infra (no boto3/grpc/httpx here).

See ../../../PLAN.md section 3 for the design rationale (Ports & Adapters).
"""

from __future__ import annotations

import enum
import time
from dataclasses import dataclass, field


class RecordingStatus(str, enum.Enum):
    RECORDING = "recording"
    GRACE_WAIT = "grace_wait"      # PLAN.md D5 tier 2: stream dropped abruptly, waiting for reconnect
    FINALIZING = "finalizing"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class UploadedPart:
    part_number: int
    etag: str


@dataclass(frozen=True)
class QualityAnnotation:
    """PLAN.md D12: observation only, never causes record-service to discard data."""

    start_offset_ms: int
    reason: str
    end_offset_ms: int | None = None  # filled in when the session finalizes


@dataclass
class RecordingSession:
    """One (room_id, track_id) capture. Maps 1:1 to a single S3 multipart upload."""

    room_id: str
    track_id: str
    participant_identity: str
    source: str
    sample_rate: int
    channels: int
    bucket: str
    object_key: str

    upload_id: str = ""
    status: RecordingStatus = RecordingStatus.RECORDING
    parts: list[UploadedPart] = field(default_factory=list)

    raw_bytes_received: int = 0
    frames_received: int = 0
    dropped_frame_count: int = 0
    quality_annotations: list[QualityAnnotation] = field(default_factory=list)

    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    reported: bool = False
    report_attempts: int = 0
    last_report_error: str | None = None

    @staticmethod
    def make_session_id(room_id: str, track_id: str) -> str:
        return f"{room_id}:{track_id}"

    @property
    def session_id(self) -> str:
        return self.make_session_id(self.room_id, self.track_id)

    @property
    def next_part_number(self) -> int:
        return len(self.parts) + 1

    def bit_depth_bytes(self) -> int:
        return 2  # PCM16

    def expected_bytes(self, at: float | None = None) -> float:
        """PLAN.md D11: sanity-check baseline, elapsed time x nominal byte rate."""
        elapsed = (at or self.ended_at or time.time()) - self.started_at
        bytes_per_second = self.sample_rate * self.channels * self.bit_depth_bytes()
        return max(0.0, elapsed) * bytes_per_second

    def is_byte_rate_suspect(self, tolerance: float) -> bool:
        expected = self.expected_bytes()
        if expected <= 0:
            return False
        return self.raw_bytes_received < expected * tolerance

    def drop_rate(self) -> float:
        """PLAN.md D12: cumulative drop ratio, used as the rolling-degradation signal."""
        total_seen = self.frames_received + self.dropped_frame_count
        if total_seen <= 0:
            return 0.0
        return self.dropped_frame_count / total_seen
