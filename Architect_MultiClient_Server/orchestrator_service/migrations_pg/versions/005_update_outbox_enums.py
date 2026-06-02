"""update outbox tasks to use enums

Revision ID: 005_update_outbox_enums
Revises: 004_add_outbox_tasks
Create Date: 2026-06-02 14:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '005_update_outbox_enums'
down_revision = '004_add_outbox_tasks'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # 1. Create Enums
    use_case_enum = postgresql.ENUM('retry_summarization', name='outboxusecase')
    use_case_enum.create(op.get_bind(), checkfirst=True)
    
    status_enum = postgresql.ENUM('pending', 'processing', 'completed', 'failed', name='outboxstatus')
    status_enum.create(op.get_bind(), checkfirst=True)
    
    retry_type_enum = postgresql.ENUM('summary', 'action_items', 'all', name='retrytype')
    retry_type_enum.create(op.get_bind(), checkfirst=True)
    
    # 2. Add retry_type column
    op.add_column('outbox_tasks', sa.Column('retry_type', retry_type_enum, nullable=True))
    
    # 3. Alter existing columns from Text to Enum
    # Drop defaults first to avoid DatatypeMismatchError
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN use_case DROP DEFAULT")
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status DROP DEFAULT")
    
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN use_case TYPE outboxusecase USING use_case::outboxusecase")
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status TYPE outboxstatus USING status::outboxstatus")
    
    # Restore defaults
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status SET DEFAULT 'pending'::outboxstatus")

def downgrade() -> None:
    # Revert columns to Text
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN use_case DROP DEFAULT")
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status DROP DEFAULT")
    
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN use_case TYPE TEXT USING use_case::text")
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status TYPE TEXT USING status::text")
    
    op.execute("ALTER TABLE outbox_tasks ALTER COLUMN status SET DEFAULT 'pending'")
    
    # Drop retry_type
    op.drop_column('outbox_tasks', 'retry_type')
    
    # Drop Enums
    op.execute("DROP TYPE outboxusecase")
    op.execute("DROP TYPE outboxstatus")
    op.execute("DROP TYPE retrytype")
