"""
Room and Participant Data Models for Orchestrator Service API v2
Independent of LiveKit SDK dependencies.
"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class AudioTrackInfo(BaseModel):  # type: ignore[explicit-any]
    """Model representing audio track information."""

    participant_identity: str = Field(..., description="Participant identity")
    filename: str = Field(..., description="Audio track filename")
    started_at_ns: int | str | None = Field(default=None, description="Audio track started at (nanoseconds)")
    ended_at_ns: int | str | None = Field(default=None, description="Audio track ended at (nanoseconds)")


class ParticipantModel(BaseModel):  # type: ignore[explicit-any]
    """Model representing participant details."""

    identity: str = Field(..., description="Identity of the participant")
    name: str = Field(..., description="Name of the participant")
    state: str = Field(..., description="State of the participant")
    joined_at: int = Field(..., description="Timestamp when participant joined")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Metadata of the participant"
    )


class ParticipantListResponseModel(BaseModel):  # type: ignore[explicit-any]
    """Model representing participant list response."""

    status: str = Field(default="ok", description="Status of the operation")
    participants: list[ParticipantModel] = Field(default_factory=list, description="List of participants")


class ParticipantBasicInfo(BaseModel):
    identity: str
    name: str | None = None
    joined_at: datetime | None = None


class RoomDetailResponseModel(BaseModel):
    room_name: str
    room_id: str
    status: str
    created_at: datetime
    participants_count: int = 0
    audio_tracks_count: int = 0
    transcripts_count: int = 0


class RoomListResponseModel(BaseModel):
    total: int
    skip: int
    limit: int
    rooms: list[RoomDetailResponseModel]


class RoomAudioInfoResponseModel(BaseModel):
    room_id: str
    audio_files: list[AudioTrackInfo]
    total_files: int


class RoomStatisticsResponseModel(BaseModel):
    room_id: str
    room_name: str
    duration_seconds: float = 0.0
    total_audio_tracks: int = 0
    total_transcripts: int = 0
    total_summary_items: int = 0
