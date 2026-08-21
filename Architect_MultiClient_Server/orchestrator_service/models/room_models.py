"""
Pydantic models for room data, participant, audio track, and dispatch DTOs.
"""

from typing import Any

from pydantic import BaseModel, Field


class ParticipantBasicInfo(BaseModel):  # type: ignore[explicit-any]
    """Model representing basic participant information."""

    identity: str = Field(..., description="Identity of the participant")
    name: str = Field(..., description="Name of the participant")
    state: str = Field(..., description="State of the participant")
    joined_at: int = Field(..., description="Timestamp when participant joined")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] = Field(  # type: ignore[explicit-any]
        ..., description="Metadata of the participant"
    )


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
    metadata: dict[str, Any] = Field(  # type: ignore[explicit-any]
        default_factory=dict, description="Metadata of the participant"
    )


class ParticipantListResponseModel(BaseModel):  # type: ignore[explicit-any]
    """Model representing participant list response."""

    status: str = Field(default="ok", description="Status of the operation")
    participants: list[ParticipantModel] = Field(default_factory=list, description="List of participants")
