"""remove obsolete derivative tracking

Revision ID: 008_remove_derivative_tracking
Revises: 007_add_correction_progress
Create Date: 2026-11-09 00:00:00.000000

Record-service now stores the original capture as client-playable Ogg/Opus,
so tracks no longer need a separate derivative lifecycle.
"""

import sqlalchemy as sa
from alembic import op

revision = "008_remove_derivative_tracking"
down_revision = "007_add_correction_progress"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_tracks_derivative_status", table_name="tracks")
    op.drop_column("tracks", "derivative_status")


def downgrade() -> None:
    op.add_column("tracks", sa.Column("derivative_status", sa.Text(), nullable=True))
    op.create_index("ix_tracks_derivative_status", "tracks", ["derivative_status"])
