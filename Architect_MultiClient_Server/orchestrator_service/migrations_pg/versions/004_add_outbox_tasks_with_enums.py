"""add outbox tasks table with enums

Revision ID: 004_add_outbox_tasks_with_enums
Revises: 003_drop_full_text_column
Create Date: 2026-06-02 15:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "004_add_outbox_tasks_with_enums"
down_revision = "003_drop_full_text_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create enum types idempotently (checkfirst=True skips if already exists)
    postgresql.ENUM("retry_summarization", name="outboxusecase").create(op.get_bind(), checkfirst=True)
    postgresql.ENUM("pending", "processing", "completed", "failed", name="outboxstatus").create(
        op.get_bind(), checkfirst=True
    )

    # create_type=False prevents create_table from trying to CREATE TYPE again
    use_case_enum = postgresql.ENUM("retry_summarization", name="outboxusecase", create_type=False)
    status_enum = postgresql.ENUM(
        "pending", "processing", "completed", "failed", name="outboxstatus", create_type=False
    )

    op.create_table(
        "outbox_tasks",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("use_case", use_case_enum, nullable=False),
        sa.Column("status", status_enum, nullable=False, server_default="pending"),
        sa.Column("configs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_index("ix_outbox_tasks_status_created_at", "outbox_tasks", ["status", "created_at"])
    op.create_index("ix_outbox_tasks_use_case", "outbox_tasks", ["use_case"])


def downgrade() -> None:
    op.drop_index("ix_outbox_tasks_status_created_at", table_name="outbox_tasks")
    op.drop_index("ix_outbox_tasks_use_case", table_name="outbox_tasks")

    op.drop_table("outbox_tasks")
    op.execute("DROP TYPE outboxusecase")
    op.execute("DROP TYPE outboxstatus")
