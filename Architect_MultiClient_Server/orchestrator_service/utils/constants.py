"""Cross-service string contracts (audio-ingestion PLAN.md D3x).

Not import-shareable with agents/ or record-service/ (separate deployables,
separate venvs) -- mirror any change here in
agents/src/config/constants.py too.
"""

# Stable track_id the agent uses when opening the record-service session for
# its own TTS output (agents/src/core/tts_manager.py). Recognized here to
# skip Whisper STT for this one track -- its text is already known (the
# agent received it from orchestrator's own agent-control call in the first
# place), so transcribing the agent's own synthesized voice back into text
# would be redundant, and at the wrong sample rate to boot (TTS is 24kHz,
# Whisper's feature extractor is fixed at 16kHz).
AGENT_TTS_TRACK_ID = "agent-tts"

# Posted by the agent directly to POST /api/v2/recordings/events (same
# dispatcher record-service/audio-processing-service use) in place of the
# Whisper round-trip for its own track.
TTS_TRANSCRIPT_EVENT = "tts.transcript"
TTS_COMPLETED_EVENT = "tts.completed"


def make_track_ref_id(room_id: str, track_id: str) -> str:
    """Matches record-service's RecordingSession.make_session_id -- this is
    the tracks.id primary key (PLAN.md D10 defers the PK rename)."""
    return f"{room_id}:{track_id}"
