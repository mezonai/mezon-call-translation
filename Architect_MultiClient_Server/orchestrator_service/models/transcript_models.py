"""
Pydantic models for transcript API

Data models for request/response handling in transcript endpoints.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel

# ============================================================================
# Response Models
# ============================================================================


class RoomResponse(BaseModel):              # type: ignore[explicit-any]
    """Response model for room data"""

    room_name: str
    status: str | None = None
    total_tracks: int | None = None
    completed_tracks: int | None = None
    remain_tracks: int | None = None
    created_at: datetime | None = None
    completed_at: datetime | None = None


class TrackResponse(BaseModel):             # type: ignore[explicit-any]
    """Response model for track data"""

    egress_id: str
    track_id: str | None = None
    participant_identity: str | None = None
    status: str | None = None
    created_at: datetime | None = None


class TranscriptSegment(BaseModel):         # type: ignore[explicit-any]
    """Model for a transcript segment"""

    start: float | None = None
    end: float | None = None
    text: str | None = None
    confidence: float | None = None


class ChunkResponse(BaseModel):             # type: ignore[explicit-any]
    """Response model for transcript chunk"""

    chunk_index: int
    item_count: int | None = None
    start_time: float | None = None
    end_time: float | None = None
    segments: list[dict[str, Any]] | None = None


class RoomStatisticsResponse(BaseModel):    # type: ignore[explicit-any]
    """Response model for room statistics"""

    room_name: str
    status: str | None = None
    total_tracks: int = 0
    completed_tracks: int = 0
    remain_tracks: int = 0
    total_duration_sec: float = 0.0
    total_segments: int = 0
    created_at: datetime | None = None
    completed_at: datetime | None = None


class ParticipantStatisticsResponse(BaseModel):  # type: ignore[explicit-any]
    """Response model for participant statistics"""

    participant_identity: str
    total_tracks: int = 0
    unique_rooms: int = 0
    total_duration_sec: float = 0.0
    total_segments: int = 0


class SearchResultResponse(BaseModel):      # type: ignore[explicit-any]
    """Response model for transcript search results"""

    chunk_index: int
    segment: dict[str, Any]


class PaginatedResponse(BaseModel):         # type: ignore[explicit-any]
    """Base model for paginated responses"""

    status: str = "ok"
    total: int
    limit: int
    skip: int


class RoomListResponse(PaginatedResponse):  # type: ignore[explicit-any]
    """Response model for room list"""

    rooms: list[dict[str, Any]]
    date_range: dict[str, str] | None = None


class TrackListResponse(PaginatedResponse): # type: ignore[explicit-any]
    """Response model for track list"""

    tracks: list[dict[str, Any]]
    date_range: dict[str, str] | None = None


class ChunkListResponse(BaseModel):         # type: ignore[explicit-any]
    """Response model for chunk list"""

    status: str = "ok"
    track_id: str
    total_chunks: int
    chunks: list[dict[str, Any]]


class FullTranscriptResponse(BaseModel):    # type: ignore[explicit-any]
    """Response model for full transcript"""

    status: str = "ok"
    track_id: str
    total_segments: int
    transcript: list[dict[str, Any]]


class SearchResponse(BaseModel):            # type: ignore[explicit-any]
    """Response model for search results"""

    status: str = "ok"
    track_id: str
    query: str
    total_matches: int
    results: list[dict[str, Any]]


class ConfidenceFilterResponse(BaseModel):  # type: ignore[explicit-any]
    """Response model for confidence filtered segments"""

    status: str = "ok"
    track_id: str
    confidence_range: dict[str, float]
    total_segments: int
    segments: list[dict[str, Any]]


class HealthCheckResponse(BaseModel):       # type: ignore[explicit-any]
    """Response model for health check"""

    status: str
    service: str
    mongodb_connected: bool
    timestamp: str
    error: str | None = None
