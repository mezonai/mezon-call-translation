"""drop tracks.derivative_status (audio-processing-service retired)

Revision ID: 007_drop_derivative_tracking
Revises: 006_add_rooms_section_summary
Create Date: 2026-09-14 00:00:00.000000

See audio-ingestion/PLAN.md D32: record-service now encodes PCM->OGG/Opus
itself on its live ingest path and reports the final, client-playable
artifact directly via `recording.completed` -- there is no more separate
derivative-transcode stage (`audio-processing-service`, retired) for
`tracks.derivative_status` (added in 005_add_derivative_tracking) to track.

`check_and_notify_room_recordings_ready()` (pg_transcript_repository.py),
the only reader of this column, now gates on `tracks.status != 'pending'`
instead -- `status` already reaches a terminal value ("wait_process" /
"failed") at exactly the same point `derivative_status` used to (see that
method's docstring for the full reasoning), so no other schema change is
needed to preserve its behavior.

`rooms.record_notified_at` (also added in 005) is untouched here -- it's
still the idempotency guard for `room_record_done`, independent of the
derivative pipeline.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "007_drop_derivative_tracking"
down_revision = "006_add_rooms_section_summary"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_tracks_derivative_status", table_name="tracks")
    op.drop_column("tracks", "derivative_status")


def downgrade() -> None:
    op.add_column("tracks", sa.Column("derivative_status", sa.Text(), nullable=True))
    op.create_index("ix_tracks_derivative_status", "tracks", ["derivative_status"])
