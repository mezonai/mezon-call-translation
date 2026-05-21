"""calculate participant durations

Revision ID: 002_calculate_participant_durations
Revises: 001_initial_schema
Create Date: 2026-05-21 16:30:00.000000

"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002_calc_part_durations'
down_revision = '001_initial_schema'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Update participants array in rooms table with calculated duration
    # This handles adding a duration field to each participant in the JSONB array.
    op.execute(
        sa.text(
            """
            WITH participant_durations AS (
                SELECT 
                    t.room_ref_id,
                    t.participant_identity,
                    COALESCE(SUM(tc.end_time - tc.start_time), 0.0) AS duration
                FROM 
                    tracks t
                JOIN 
                    transcript_chunks tc ON t.id = tc.track_ref_id
                GROUP BY 
                    t.room_ref_id, t.participant_identity
            )
            UPDATE rooms r
            SET participants = (
                SELECT jsonb_agg(
                    CASE 
                        WHEN pd.participant_identity IS NOT NULL THEN 
                            p || jsonb_build_object('duration', pd.duration)
                        ELSE 
                            p || jsonb_build_object('duration', 0.0)
                    END
                )
                FROM jsonb_array_elements(r.participants) AS p
                LEFT JOIN participant_durations pd 
                    ON pd.room_ref_id = r.id 
                    AND pd.participant_identity = (p->>'participant_identity')
            )
            WHERE r.participants IS NOT NULL AND jsonb_array_length(r.participants) > 0;
            """
        )
    )


def downgrade() -> None:
    # Remove duration from participants array
    op.execute(
        sa.text(
            """
            UPDATE rooms
            SET participants = (
                SELECT jsonb_agg(p - 'duration')
                FROM jsonb_array_elements(participants) AS p
            )
            WHERE participants IS NOT NULL AND jsonb_array_length(participants) > 0;
            """
        )
    )
