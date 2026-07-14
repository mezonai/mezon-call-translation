"""add rooms section summary table

Revision ID: 005_add_rooms_section_summary
Revises: 004_add_outbox_tasks_with_enums
Create Date: 2026-07-01 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "005_add_rooms_section_summary"
down_revision = "004_add_outbox_tasks_with_enums"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rooms_section_summary",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("room_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("room_name", sa.Text(), nullable=True),
        sa.Column("section_index", sa.Integer(), nullable=True),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("summary_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("start_time", sa.Text(), nullable=True),
        sa.Column("end_time", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index(
        "ix_rooms_section_summary_room_id",
        "rooms_section_summary",
        ["room_id"],
        unique=False,
    )
    op.create_index(
        "ix_rooms_section_summary_room_name",
        "rooms_section_summary",
        ["room_name"],
        unique=False,
    )
    op.create_index(
        "ix_rooms_section_summary_room_id_section_index",
        "rooms_section_summary",
        ["room_id", "section_index"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_rooms_section_summary_room_id_section_index", table_name="rooms_section_summary")
    op.drop_index("ix_rooms_section_summary_room_name", table_name="rooms_section_summary")
    op.drop_index("ix_rooms_section_summary_room_id", table_name="rooms_section_summary")
    op.drop_table("rooms_section_summary")
