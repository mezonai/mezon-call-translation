"""add correction progress

Revision ID: 007_add_correction_progress
Revises: 006_add_rooms_section_summary
Create Date: 2026-08-13 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "007_add_correction_progress"
down_revision = "006_add_rooms_section_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "rooms_summary",
        sa.Column("correction_progress", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rooms_summary", "correction_progress")
