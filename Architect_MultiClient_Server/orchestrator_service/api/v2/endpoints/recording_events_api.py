"""
Endpoint record-service posts its lifecycle events to. audio-processing-service
and its derivative.completed/.failed events are retired (PLAN.md
D6-successor) -- record-service now reports the final OGG/Opus artifact
directly via recording.completed. See audio-ingestion/PLAN.md D8 (idempotent,
not shaped like a LiveKit egress webhook) and D18/D19 (what each event does
to Track/Room state).

Path matches record-service's default OrchestratorConfig.events_path
(record-service/src/record_service/config.py).
"""

from fastapi import APIRouter, Depends

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.recording_event_models import (
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
    same payload -- save_track_metadata upserts, and
    check_and_notify_room_recordings_ready only ever fires once per room.
    """

    if isinstance(body, RecordingEventRequest):
        return await recording_event_service.handle_recording_event(body)

    return await recording_event_service.handle_tts_transcript_event(body)
