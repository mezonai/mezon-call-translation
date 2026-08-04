"""
Request models for POST /api/v2/recordings/events.

Deliberately NOT shaped like a LiveKit egress webhook (audio-ingestion
PLAN.md D2) -- this is the clean, self-describing contract record-service
and (later, Phase 5) audio-processing-service post to. Field names match
record-service's HttpEventReporter._to_payload() exactly
(record-service/src/record_service/infra/reporting/http_event_reporter.py).
"""


from pydantic import BaseModel


class QualityAnnotationModel(BaseModel):  # type: ignore[explicit-any]
    start_offset_ms: int
    end_offset_ms: int | None = None
    reason: str


class RecordingEventRequest(BaseModel):  # type: ignore[explicit-any]
    """Posted by record-service. event: "recording.started" | "recording.completed" | "recording.failed"."""

    event: str
    recording_id: str          # room_id:track_id -- used as tracks.id (PLAN.md D10 defers the PK rename)
    room_id: str                # normally orchestrator's own stable room UUID, captured by the agent at
                                 # registration and threaded through record-service unchanged (PLAN.md D27,
                                 # supersedes D18) -- see RecordingEventService._resolve_room_ref_id for the
                                 # degrade path (falls back to LiveKit room-name resolve) if it isn't a UUID
    track_id: str
    participant_identity: str
    source: str
    sample_rate: int
    channels: int
    bucket: str
    object_key: str
    status: str
    started_at: float
    ended_at: float | None = None
    duration_seconds: float | None = None
    raw_bytes_received: int = 0
    dropped_frame_count: int = 0
    quality_annotations: list[QualityAnnotationModel] = []


class DerivativeEventRequest(BaseModel):  # type: ignore[explicit-any]
    """Posted by audio-processing-service (Phase 5). event: "derivative.completed" | "derivative.failed"."""

    event: str
    recording_id: str          # matches tracks.id from the originating RecordingEventRequest
    bucket: str | None = None
    object_key: str | None = None
    error: str | None = None


class RecordingEventResponse(BaseModel):  # type: ignore[explicit-any]
    received: bool
    action: str | None = None
    error: str | None = None
