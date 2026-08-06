"""Cross-service string contracts (audio-ingestion PLAN.md D3x).

Not import-shareable with orchestrator_service/ (separate deployable,
separate venv) -- mirror any change here in
orchestrator_service/utils/constants.py too.
"""

# Stable track_id used when opening the record-service session for the
# agent's own TTS output (see core/tts_manager.py). orchestrator_service's
# recording_event_service.py matches on this exact string to recognize the
# agent's own track and skip Whisper STT for it.
AGENT_TTS_TRACK_ID = "agent-tts"

# Posted to POST /api/v2/recordings/events in place of the Whisper round-trip
# for the agent's own track (text is already known -- came from
# orchestrator's own agent-control call in the first place).
TTS_TRANSCRIPT_EVENT = "tts.transcript"
TTS_COMPLETED_EVENT = "tts.completed"
