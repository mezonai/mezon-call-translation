"""add derivative tracking (tracks.derivative_status, rooms.record_notified_at)

Revision ID: 005_add_derivative_tracking
Revises: 004_add_outbox_tasks_with_enums
Create Date: 2026-07-29 00:00:00.000000

See audio-ingestion/PLAN.md D18/D19 -- record-service's raw capture and
audio-processing-service's client-playable derivative are two independent
lifecycles for the same track. `tracks.status` already tracks the STT
(Whisper) pipeline; `derivative_status` tracks the separate transcode
pipeline so `room_record_done` can be gated on it without touching STT
semantics. `rooms.record_notified_at` is the idempotency guard for firing
that event exactly once per room (mirrors the `status='pending'` guard
pattern already used by `final_room_status()`).

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = '005_add_derivative_tracking'
down_revision = '004_add_outbox_tasks_with_enums'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('tracks', sa.Column('derivative_status', sa.Text(), nullable=True))
    op.create_index('ix_tracks_derivative_status', 'tracks', ['derivative_status'])

    op.add_column('rooms', sa.Column('record_notified_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column('rooms', 'record_notified_at')

    op.drop_index('ix_tracks_derivative_status', table_name='tracks')
    op.drop_column('tracks', 'derivative_status')
