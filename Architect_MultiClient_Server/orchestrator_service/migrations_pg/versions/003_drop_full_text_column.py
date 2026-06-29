"""drop full_text column

Revision ID: 003_drop_full_text_column
Revises: 002_calc_part_durations
Create Date: 2026-05-26 16:35:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "003_drop_full_text_column"
down_revision = "002_calc_part_durations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop full_text column from rooms_summary table
    op.drop_column("rooms_summary", "full_text")


def downgrade() -> None:
    import json

    # Re-add full_text column to rooms_summary table
    op.add_column("rooms_summary", sa.Column("full_text", sa.Text(), nullable=True))

    # Rebuild full_text in Python to avoid asyncpg issues with JSONB ->> operator.
    # Format: "[{timestamp}] {participant_id}: {content}" joined by newline.
    bind = op.get_bind()
    result = bind.execute(sa.text("SELECT id, messages FROM rooms_summary WHERE messages IS NOT NULL"))

    for row in result:
        try:
            messages = row[1]
            # asyncpg may return JSONB as dict/list directly, or as a string
            if isinstance(messages, str):
                messages = json.loads(messages)
            if not isinstance(messages, list) or len(messages) == 0:
                continue
            full_text = "\n".join(
                f"[{t.get('timestamp', '')}] {t.get('participant_id', '')}: {t.get('content', '')}"
                for t in messages
                if isinstance(t, dict)
            )
            if not full_text:
                continue
            bind.execute(
                sa.text("UPDATE rooms_summary SET full_text = :ft WHERE id = :id"),
                {"ft": full_text, "id": row[0]},
            )
        except Exception:
            # Skip rows with malformed/unexpected messages data
            continue
