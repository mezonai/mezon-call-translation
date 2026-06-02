"""
SQLAlchemy ORM models for PostgreSQL.

Design decisions:
- UUID (as TEXT) for all synthetic PKs; tracks use egress_id (TEXT) as PK
- mongo_id TEXT column on rooms for migration mapping (ObjectId → UUID)
- Complex fields stored as JSONB; validated in application code
- No foreign-key constraints, no heavy NOT NULL constraints (except PK)
- PostgreSQL 16+
"""

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Float, Integer, Text, DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# rooms
# ---------------------------------------------------------------------------
class Room(Base):
    """
    Corresponds to MongoDB 'rooms' collection.
    mongo_id stores the original ObjectId string for post-migration reference.
    participants stored as JSONB array:
        [{"participant_identity": "...", "timestamp": "<iso8601>"}]
    """

    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    mongo_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # migration mapping
    room_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    participants: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=list
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finalized_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_rooms_room_name", "room_name"),
        Index("ix_rooms_status", "status"),
        Index("ix_rooms_created_at", "created_at"),
        Index("ix_rooms_mongo_id", "mongo_id"),
    )


# ---------------------------------------------------------------------------
# tracks
# ---------------------------------------------------------------------------
class Track(Base):
    """
    Corresponds to MongoDB 'tracks' collection.
    PK = egress_id (same as MongoDB '_id').
    audio_info stored as JSONB:
        {filename, duration_sec, started_at_ns, ended_at_ns, location, source}
    """

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # egress_id
    track_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    room_ref_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True
    )  # UUID of rooms.id
    participant_identity: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    audio_info: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_tracks_room_ref_id", "room_ref_id"),
        Index("ix_tracks_participant_identity", "participant_identity"),
        Index("ix_tracks_status", "status"),
        Index("ix_tracks_created_at", "created_at"),
    )


# ---------------------------------------------------------------------------
# transcript_chunks
# ---------------------------------------------------------------------------
class TranscriptChunk(Base):
    """
    Corresponds to MongoDB 'transcript_chunks' collection.
    segments stored as JSONB array of segment objects.
    """

    __tablename__ = "transcript_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    track_ref_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # egress_id
    chunk_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    start_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    end_time: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    item_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    segments: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        Index("ix_chunks_track_ref_id", "track_ref_id"),
        Index("ix_chunks_track_chunk_index", "track_ref_id", "chunk_index"),
    )


# ---------------------------------------------------------------------------
# rooms_summary
# ---------------------------------------------------------------------------
class RoomSummary(Base):
    """
    Corresponds to MongoDB 'rooms_summary' collection.
    summary_data and messages stored as JSONB.
    participants stored as JSONB array of identity strings.
    """

    __tablename__ = "rooms_summary"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        nullable=True
    )  # UUID of rooms.id
    room_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    participants: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    summary_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    messages: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    total_segments: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_rooms_summary_room_id", "room_id"),
        Index("ix_rooms_summary_room_name", "room_name"),
    )


# ---------------------------------------------------------------------------
# metadata_events
# ---------------------------------------------------------------------------
class MetadataEvent(Base):
    """
    Corresponds to MongoDB 'metadata_events' collection.
    metadata stored as JSONB.
    """

    __tablename__ = "metadata_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )  # UUID string from app
    event_type: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    room_id: Mapped[Optional[uuid.UUID]] = mapped_column(nullable=True)
    room_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    event_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata", JSONB, nullable=True
    )
    timestamp: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("ix_events_event_type", "event_type"),
        Index("ix_events_room_id", "room_id"),
        Index("ix_events_created_at", "created_at"),
        Index("ix_events_event_id", "event_id", unique=True),
    )


# ---------------------------------------------------------------------------
# users
# ---------------------------------------------------------------------------
class User(Base):
    """
    Corresponds to MongoDB 'users' collection.
    permissions stored as JSONB array of strings.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # user_id from Mezon
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    display_name: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    permissions: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=list
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (Index("ix_users_username", "username"),)


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------
class RefreshToken(Base):
    """
    Corresponds to MongoDB 'refresh_tokens' collection.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    refresh_token_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    access_token_jti: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    device_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_revoked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        Index("ix_refresh_tokens_hash", "refresh_token_hash"),
        Index("ix_refresh_tokens_user_id", "user_id"),
        Index("ix_refresh_tokens_expires_at", "expires_at"),
    )


# ---------------------------------------------------------------------------
# token_blacklist
# ---------------------------------------------------------------------------
class TokenBlacklist(Base):
    """
    Corresponds to MongoDB 'token_blacklist' collection.
    """

    __tablename__ = "token_blacklist"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jti: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    token_hash: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    blacklisted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_blacklist_jti", "jti", unique=True),
        Index("ix_blacklist_expires_at", "expires_at"),
    )


# ---------------------------------------------------------------------------
# outbox_tasks
# ---------------------------------------------------------------------------
class OutboxTask(Base):
    """
    Generic Outbox table to store and manage asynchronous retry tasks.
    """

    __tablename__ = "outbox_tasks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    use_case: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="pending")
    configs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_outbox_tasks_status_created_at", "status", "created_at"),
        Index("ix_outbox_tasks_use_case", "use_case"),
    )
