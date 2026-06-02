"""add outbox tasks table

Revision ID: 004_add_outbox_tasks
Revises: 003_drop_full_text_column
Create Date: 2026-06-01 12:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = '004_add_outbox_tasks'
down_revision = '003_drop_full_text_column'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create outbox_tasks table
    op.create_table(
        'outbox_tasks',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('use_case', sa.Text(), nullable=False),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('configs', postgresql.JSONB(), nullable=False, server_default='{}'),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint('id')
    )

    # Create Indexes
    op.create_index(
        'ix_outbox_tasks_status_created_at',
        'outbox_tasks',
        ['status', 'created_at']
    )
    op.create_index(
        'ix_outbox_tasks_use_case',
        'outbox_tasks',
        ['use_case']
    )


def downgrade() -> None:
    # Drop Indexes
    op.drop_index('ix_outbox_tasks_status_created_at', table_name='outbox_tasks')
    op.drop_index('ix_outbox_tasks_use_case', table_name='outbox_tasks')

    # Drop table
    op.drop_table('outbox_tasks')
