"""initial_schema

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-05-16 10:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # rooms
    op.create_table(
        "rooms",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("mongo_id", sa.Text(), nullable=True),
        sa.Column("room_name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rooms_room_name", "rooms", ["room_name"], unique=False)
    op.create_index("ix_rooms_status", "rooms", ["status"], unique=False)
    op.create_index("ix_rooms_created_at", "rooms", ["created_at"], unique=False)
    op.create_index("ix_rooms_mongo_id", "rooms", ["mongo_id"], unique=False)

    # tracks
    op.create_table(
        "tracks",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("track_id", sa.Text(), nullable=True),
        sa.Column("room_ref_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("participant_identity", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("audio_info", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_tracks_room_ref_id", "tracks", ["room_ref_id"], unique=False)
    op.create_index(
        "ix_tracks_participant_identity",
        "tracks",
        ["participant_identity"],
        unique=False,
    )
    op.create_index("ix_tracks_status", "tracks", ["status"], unique=False)
    op.create_index("ix_tracks_created_at", "tracks", ["created_at"], unique=False)

    # transcript_chunks
    op.create_table(
        "transcript_chunks",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("track_ref_id", sa.Text(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=True),
        sa.Column("start_time", sa.Float(), nullable=True),
        sa.Column("end_time", sa.Float(), nullable=True),
        sa.Column("item_count", sa.Integer(), nullable=True),
        sa.Column("segments", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_chunks_track_ref_id", "transcript_chunks", ["track_ref_id"], unique=False)
    op.create_index(
        "ix_chunks_track_chunk_index",
        "transcript_chunks",
        ["track_ref_id", "chunk_index"],
        unique=False,
    )

    # rooms_summary
    op.create_table(
        "rooms_summary",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("room_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("room_name", sa.Text(), nullable=True),
        sa.Column("participants", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("full_text", sa.Text(), nullable=True),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("total_segments", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rooms_summary_room_id", "rooms_summary", ["room_id"], unique=False)
    op.create_index("ix_rooms_summary_room_name", "rooms_summary", ["room_name"], unique=False)

    # metadata_events
    op.create_table(
        "metadata_events",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("event_id", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=True),
        sa.Column("room_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("room_name", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("timestamp", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_events_event_type", "metadata_events", ["event_type"], unique=False)
    op.create_index("ix_events_room_id", "metadata_events", ["room_id"], unique=False)
    op.create_index("ix_events_created_at", "metadata_events", ["created_at"], unique=False)
    op.create_index("ix_events_event_id", "metadata_events", ["event_id"], unique=True)

    # users
    op.create_table(
        "users",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("username", sa.Text(), nullable=True),
        sa.Column("display_name", sa.Text(), nullable=True),
        sa.Column("avatar_url", sa.Text(), nullable=True),
        sa.Column("permissions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=False)

    # refresh_tokens
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("refresh_token_hash", sa.Text(), nullable=True),
        sa.Column("access_token_jti", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("device_info", sa.Text(), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default="false"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_refresh_tokens_hash", "refresh_tokens", ["refresh_token_hash"], unique=False)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"], unique=False)
    op.create_index("ix_refresh_tokens_expires_at", "refresh_tokens", ["expires_at"], unique=False)

    # token_blacklist
    op.create_table(
        "token_blacklist",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("jti", sa.Text(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("token_hash", sa.Text(), nullable=True),
        sa.Column("blacklisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_blacklist_jti", "token_blacklist", ["jti"], unique=True)
    op.create_index("ix_blacklist_expires_at", "token_blacklist", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_table("token_blacklist")
    op.drop_table("refresh_tokens")
    op.drop_table("users")
    op.drop_table("metadata_events")
    op.drop_table("rooms_summary")
    op.drop_table("transcript_chunks")
    op.drop_table("tracks")
    op.drop_table("rooms")
