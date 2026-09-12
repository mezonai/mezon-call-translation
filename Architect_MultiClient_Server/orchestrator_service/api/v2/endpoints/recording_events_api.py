"""
Endpoint record-service (Phase 1-2) and, from Phase 5, audio-processing-service
post their lifecycle events to. See audio-ingestion/PLAN.md D8 (idempotent,
not shaped like a LiveKit egress webhook) and D18/D19 (what each event does
to Track/Room state).

Path matches record-service's default OrchestratorConfig.events_path
(record-service/src/record_service/config.py).
"""

from fastapi import APIRouter, Depends

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.recording_event_models import (
    DerivativeEventRequest,
    RecordingEventPayload,
    RecordingEventRequest,
    RecordingEventResponse,
)
from orchestrator_service.services.recording_event_service import RecordingEventService

router = APIRouter(prefix="/recordings", tags=["recording events"])

recording_event_service = RecordingEventService()


@router.post("/events", response_model=RecordingEventResponse)
async def recording_events_endpoint(  # type: ignore[explicit-any]
    body: RecordingEventPayload,
    auth: dict[str, str | bool] = Depends(verify_api_key),
) -> RecordingEventResponse:
    """
    Single entrypoint for both event families, dispatched on `event`.
    Idempotent by design (PLAN.md D8): safe to call more than once with the
    same payload -- save_track_metadata/update_track_derivative upsert, and
    check_and_notify_room_recordings_ready only ever fires once per room.
    """

    if isinstance(body, RecordingEventRequest):
        return await recording_event_service.handle_recording_event(body)

    if isinstance(body, DerivativeEventRequest):
        return await recording_event_service.handle_derivative_event(body)

    return await recording_event_service.handle_tts_transcript_event(body)
