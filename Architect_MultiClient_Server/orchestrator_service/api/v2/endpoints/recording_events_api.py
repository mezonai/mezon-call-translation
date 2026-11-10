"""
Endpoint for record-service lifecycle events.

Path matches record-service's default OrchestratorConfig.events_path
(record-service/src/record_service/config.py).
"""

from fastapi import APIRouter, Depends

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.recording_event_models import (
    RecordingEventRequest,
    RecordingEventResponse,
)
from orchestrator_service.services.recording_event_service import RecordingEventService

router = APIRouter(prefix="/recordings", tags=["recording events"])

recording_event_service = RecordingEventService()


@router.post("/events", response_model=RecordingEventResponse)
async def recording_events_endpoint(  # type: ignore[explicit-any]
    body: RecordingEventRequest,
    auth: dict[str, str | bool] = Depends(verify_api_key),
) -> RecordingEventResponse:
    """
    Idempotent record-service event entrypoint.
    """

    return await recording_event_service.handle_recording_event(body)
