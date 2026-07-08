"""
SQLAlchemy ORM models for PostgreSQL.

Design decisions:
- UUID (as TEXT) for all synthetic PKs; tracks use egress_id (TEXT) as PK
- mongo_id TEXT column on rooms for migration mapping (ObjectId → UUID)
- Complex fields stored as JSONB; validated in application code
- No foreign-key constraints, no heavy NOT NULL constraints (except PK)
- PostgreSQL 16+
"""

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, Text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class OutboxUseCase(enum.StrEnum):
    RETRY_SUMMARIZATION = "retry_summarization"


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# rooms
# ---------------------------------------------------------------------------
class Room(Base):
    """
    Corresponds to PostgreSQL 'rooms' table.
    mongo_id stores the original ObjectId string for post-migration reference.
    participants stored as JSONB array:
        [{"participant_identity": "...", "timestamp": "<iso8601>"}]
    """

    __tablename__ = "rooms"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    mongo_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # migration mapping
    room_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)

    # TODO: Use `Any` type because `participants` has JSON format and default=list
    participants: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True, default=list)   # type: ignore[explicit-any]

    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    Corresponds to PostgreSQL 'tracks' table.
    PK = egress_id (same as MongoDB '_id').
    audio_info stored as JSONB:
        {filename, duration_sec, started_at_ns, ended_at_ns, location, source}
    """

    __tablename__ = "tracks"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # egress_id
    track_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    room_ref_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # UUID of rooms.id
    participant_identity: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(Text, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # TODO: Use `Any` type because `audio_info` has JSON format and complex value type hints
    audio_info: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)         # type: ignore[explicit-any]

    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    Corresponds to PostgreSQL 'transcript_chunks' table.
    segments stored as JSONB array of segment objects.
    """

    __tablename__ = "transcript_chunks"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    track_ref_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # egress_id
    chunk_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    start_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_time: Mapped[float | None] = mapped_column(Float, nullable=True)
    item_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Use `Any` type because `segments` has JSON format and complex value type hints
    segments: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)     # type: ignore[explicit-any]

    __table_args__ = (
        Index("ix_chunks_track_ref_id", "track_ref_id"),
        Index("ix_chunks_track_chunk_index", "track_ref_id", "chunk_index"),
    )


# ---------------------------------------------------------------------------
# rooms_summary
# ---------------------------------------------------------------------------
class RoomSummary(Base):
    """
    Corresponds to PostgreSQL 'rooms_summary' table.
    summary_data and messages stored as JSONB.
    participants stored as JSONB array of identity strings.
    """

    __tablename__ = "rooms_summary"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)  # UUID of rooms.id
    room_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    participants: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)

    # TODO: Use `Any` type because `summary_data` and `messages` has JSON format and complex value type hints
    summary_data: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)        # type: ignore[explicit-any]
    messages: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)      # type: ignore[explicit-any]

    total_segments: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_rooms_summary_room_id", "room_id"),
        Index("ix_rooms_summary_room_name", "room_name"),
    )


# ---------------------------------------------------------------------------
# metadata_events
# ---------------------------------------------------------------------------
class MetadataEvent(Base):
    """
    Corresponds to PostgreSQL 'metadata_events' table.
    metadata stored as JSONB.
    """

    __tablename__ = "metadata_events"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    event_id: Mapped[str | None] = mapped_column(Text, nullable=True)  # UUID string from app
    event_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    room_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    room_name: Mapped[str | None] = mapped_column(Text, nullable=True)

    # TODO: Use `Any` type because `metadata` has JSON format and complex value type hints
    event_metadata: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB, nullable=True)   # type: ignore[explicit-any]

    timestamp: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

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
    Corresponds to PostgreSQL 'users' table.
    permissions stored as JSONB array of strings.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True)  # user_id from Mezon
    username: Mapped[str | None] = mapped_column(Text, nullable=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    permissions: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True, default=list)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_users_username", "username"),)


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------
class RefreshToken(Base):
    """
    Corresponds to PostgreSQL 'refresh_tokens' table.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_jti: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    device_info: Mapped[str | None] = mapped_column(Text, nullable=True)
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
    Corresponds to PostgreSQL 'token_blacklist' table.
    """

    __tablename__ = "token_blacklist"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    jti: Mapped[str | None] = mapped_column(Text, nullable=True)
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    blacklisted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

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
    use_case: Mapped[OutboxUseCase] = mapped_column(SQLEnum(OutboxUseCase), nullable=False)
    status: Mapped[OutboxStatus] = mapped_column(SQLEnum(OutboxStatus), nullable=False, default=OutboxStatus.PENDING)

    # TODO: Use `Any` type because `configs` has JSON format and complex value type hints
    configs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)     # type: ignore[explicit-any]
    
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        Index("ix_outbox_tasks_status_created_at", "status", "created_at"),
        Index("ix_outbox_tasks_use_case", "use_case"),
    )
