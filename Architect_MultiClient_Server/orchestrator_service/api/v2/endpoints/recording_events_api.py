"""
Endpoint record-service (Phase 1-2) and, from Phase 5, audio-processing-service
post their lifecycle events to. See audio-ingestion/PLAN.md D8 (idempotent,
not shaped like a LiveKit egress webhook) and D18/D19 (what each event does
to Track/Room state).

Path matches record-service's default OrchestratorConfig.events_path
(record-service/src/record_service/config.py).
"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.recording_event_models import (
    DerivativeEventRequest,
    RecordingEventRequest,
    RecordingEventResponse,
    TtsTranscriptEventRequest,
)
from orchestrator_service.services.recording_event_service import RecordingEventService
from orchestrator_service.utils.constants import TTS_COMPLETED_EVENT, TTS_TRANSCRIPT_EVENT
from orchestrator_service.utils.logger import get_logger

router = APIRouter(prefix="/recordings", tags=["recording events"])
logger = get_logger(__name__)

recording_event_service = RecordingEventService()

_RECORDING_EVENTS = {"recording.started", "recording.completed", "recording.failed"}
_DERIVATIVE_EVENTS = {"derivative.completed", "derivative.failed"}
_TTS_EVENTS = {TTS_TRANSCRIPT_EVENT, TTS_COMPLETED_EVENT}


@router.post("/events", response_model=RecordingEventResponse)
async def recording_events_endpoint(  # type: ignore[explicit-any]
    body: dict[str, Any],
    auth: dict[str, str | bool] = Depends(verify_api_key),
) -> RecordingEventResponse:
    """
    Single entrypoint for both event families, dispatched on `event`.
    Idempotent by design (PLAN.md D8): safe to call more than once with the
    same payload -- save_track_metadata/update_track_derivative upsert, and
    check_and_notify_room_recordings_ready only ever fires once per room.
    """
    event = body.get("event", "")

    try:
        if event in _RECORDING_EVENTS:
            recording_payload = RecordingEventRequest.model_validate(body)
            return await recording_event_service.handle_recording_event(recording_payload)

        if event in _DERIVATIVE_EVENTS:
            derivative_payload = DerivativeEventRequest.model_validate(body)
            return await recording_event_service.handle_derivative_event(derivative_payload)

        if event in _TTS_EVENTS:
            tts_payload = TtsTranscriptEventRequest.model_validate(body)
            return await recording_event_service.handle_tts_transcript_event(tts_payload)

    except Exception as e:
        logger.error(f"Failed to process recording event '{event}': {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    logger.warning(f"Unknown recording event type: {event!r}")
    raise HTTPException(status_code=400, detail=f"Unknown event type: {event!r}")
