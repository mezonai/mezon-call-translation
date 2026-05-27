"""drop full_text column

Revision ID: 003_drop_full_text_column
Revises: 002_calc_part_durations
Create Date: 2026-05-26 16:35:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003_drop_full_text_column'
down_revision = '002_calc_part_durations'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop full_text column from rooms_summary table
    op.drop_column('rooms_summary', 'full_text')


def downgrade() -> None:
    # Re-add full_text column to rooms_summary table
    op.add_column('rooms_summary', sa.Column('full_text', sa.Text(), nullable=True))
