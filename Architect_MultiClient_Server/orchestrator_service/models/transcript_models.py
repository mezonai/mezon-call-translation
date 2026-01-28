"""
Pydantic models for transcript API

Data models for request/response handling in transcript endpoints.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel


# ============================================================================
# Response Models
# ============================================================================

class RoomResponse(BaseModel):
    """Response model for room data"""
    room_name: str
    status: Optional[str] = None
    total_tracks: Optional[int] = None
    completed_tracks: Optional[int] = None
    remain_tracks: Optional[int] = None
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class TrackResponse(BaseModel):
    """Response model for track data"""
    egress_id: str
    track_id: Optional[str] = None
    participant_identity: Optional[str] = None
    status: Optional[str] = None
    created_at: Optional[datetime] = None


class TranscriptSegment(BaseModel):
    """Model for a transcript segment"""
    start: Optional[float] = None
    end: Optional[float] = None
    text: Optional[str] = None
    confidence: Optional[float] = None


class ChunkResponse(BaseModel):
    """Response model for transcript chunk"""
    chunk_index: int
    item_count: Optional[int] = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None
    segments: Optional[List[Dict[str, Any]]] = None


class RoomStatisticsResponse(BaseModel):
    """Response model for room statistics"""
    room_name: str
    status: Optional[str] = None
    total_tracks: int = 0
    completed_tracks: int = 0
    remain_tracks: int = 0
    total_duration_sec: float = 0.0
    total_segments: int = 0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ParticipantStatisticsResponse(BaseModel):
    """Response model for participant statistics"""
    participant_identity: str
    total_tracks: int = 0
    unique_rooms: int = 0
    total_duration_sec: float = 0.0
    total_segments: int = 0


class SearchResultResponse(BaseModel):
    """Response model for transcript search results"""
    chunk_index: int
    segment: Dict[str, Any]


class PaginatedResponse(BaseModel):
    """Base model for paginated responses"""
    status: str = "ok"
    total: int
    limit: int
    skip: int


class RoomListResponse(PaginatedResponse):
    """Response model for room list"""
    rooms: List[Dict[str, Any]]
    date_range: Optional[Dict[str, str]] = None


class TrackListResponse(PaginatedResponse):
    """Response model for track list"""
    tracks: List[Dict[str, Any]]
    date_range: Optional[Dict[str, str]] = None


class ChunkListResponse(BaseModel):
    """Response model for chunk list"""
    status: str = "ok"
    track_id: str
    total_chunks: int
    chunks: List[Dict[str, Any]]


class FullTranscriptResponse(BaseModel):
    """Response model for full transcript"""
    status: str = "ok"
    track_id: str
    total_segments: int
    transcript: List[Dict[str, Any]]


class SearchResponse(BaseModel):
    """Response model for search results"""
    status: str = "ok"
    track_id: str
    query: str
    total_matches: int
    results: List[Dict[str, Any]]


class ConfidenceFilterResponse(BaseModel):
    """Response model for confidence filtered segments"""
    status: str = "ok"
    track_id: str
    confidence_range: Dict[str, float]
    total_segments: int
    segments: List[Dict[str, Any]]


class HealthCheckResponse(BaseModel):
    """Response model for health check"""
    status: str
    service: str
    mongodb_connected: bool
    timestamp: str
    error: Optional[str] = None
