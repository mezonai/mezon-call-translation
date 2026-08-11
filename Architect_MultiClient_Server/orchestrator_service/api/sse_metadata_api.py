"""
SSE Metadata API
Endpoints for bot to receive agent metadata events via SSE
"""

from datetime import datetime
from typing import Any, ClassVar
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.models.metadata_event_models import MetadataEventType
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Get singleton instances
metadata_channel = MetadataChannel()


# ==================== Pydantic Models ====================


class RoomInfo(BaseModel):  # type: ignore[explicit-any]
    """Room information"""

    room_id: str = Field(..., description="Room identifier")
    room_name: str = Field(..., description="Room name")

    @field_validator("room_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            UUID(v)
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid UUID format: {v!r}") from e
        return v

    class Config:
        # TODO: Use `Any` type becase json_schema_extra is defined by complex structure
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1"}
        }


class SessionStartedRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for session_started event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1"}
        }


class SessionEndedRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for session_ended event"""

    duration_seconds: int | None = Field(None, description="Duration of room session in seconds")

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Interview Room 1", "duration_seconds": 3600}
        }


class FileResult(BaseModel):  # type: ignore[explicit-any]
    """Recording file result"""

    participant_identity: str = Field(..., description="Identity of participant")
    filename: str = Field(..., description="Name of the recording file")
    start_time: str = Field(..., description="Recording start time (ISO 8601)")
    end_time: str = Field(..., description="Recording end time (ISO 8601)")

    class Config:
        json_schema_extra: ClassVar[dict[str, dict[str, str]]] = {
            "example": {
                "participant_identity": "user_1",
                "filename": "user_1_audio.mp3",
                "start_time": "2026-03-02T10:00:01Z",
                "end_time": "2026-03-02T11:00:00Z",
            }
        }


class SessionRecordDoneRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for room_record_done event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "abc123", "room_name": "Room_1"}
        }


class SessionSummaryDoneRequest(RoomInfo):  # type: ignore[explicit-any]
    """Request model for room_summary_done event"""

    class Config(RoomInfo.Config):
        json_schema_extra: ClassVar[dict[str, Any]] = {  # type: ignore[explicit-any]
            "example": {"room_id": "69a66008cfc00881f1d7b382", "room_name": "H3U-EXdDg"}
        }


# ==================== SSE Endpoint ====================


@router.get("/sse/metadata")
async def sse_metadata_endpoint(appid: str, token: str):
    """
    SSE endpoint for bot to receive agent metadata events.

    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token

    Returns:
        StreamingResponse with SSE events
    """
    account = {"appid": appid, "token": token}
    if not await authenticate_account(account):
        return HTTPException(status_code=401, detail="Account authentication failed")

    return await metadata_channel.create_connection(appid)


# ==================== Push Endpoints ====================


@router.post("/push_metadata/session_started")
async def push_session_started_api(req: SessionStartedRequest):
    """
    Push session_started event to all connected bots via SSE.

    Args:
        req: Session started event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_started(room_id=req.room_id, room_name=req.room_name)
    return result


@router.post("/push_metadata/session_ended")
async def push_session_ended_api(req: SessionEndedRequest):
    """
    Push session_ended event to all connected bots via SSE.

    Args:
        req: Session ended event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_ended(
        room_id=req.room_id, room_name=req.room_name, duration_seconds=req.duration_seconds
    )
    return result


@router.post("/push_metadata/session_record_done")
async def push_session_record_done_api(req: SessionRecordDoneRequest):
    """
    Push session_record_done event to all connected bots via SSE.
    File results are automatically fetched from MongoDB based on room_id.

    Args:
        req: Session record done event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_record_done(room_id=req.room_id, room_name=req.room_name)
    return result


@router.post("/push_metadata/session_summary_done")
async def push_session_summary_done_api(req: SessionSummaryDoneRequest):
    """
    Push session_summary_done event to all connected bots via SSE.
    Notifies that room summary/analysis has been completed.

    Args:
        req: Session summary done event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_summary_done(room_id=req.room_id, room_name=req.room_name)
    return result


@router.get("/metadata", response_description="List metadata events")
async def list_metadata_events(
    event_type: str | None = Query(
        None, description="Filter by event type (room_started, room_ended, room_record_done, room_summary_done)"
    ),
    room_id: str | None = Query(None, description="Filter by room ID"),
    from_utc: datetime | None = Query(None, description="Start of time range (UTC, ISO 8601)"),
    to_utc: datetime | None = Query(None, description="End of time range (UTC, ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    sort_order: str = Query(
        "desc", description="Sort order: 'asc' for ascending, 'desc' for descending (default: 'desc')"
    ),
):
    """
    Get metadata events with optional filters.
    Events are automatically deleted after 3 days (TTL).

    - **event_type**: Filter by event type (room_started, room_ended, room_record_done, room_summary_done)
    - **room_id**: Filter by room ID
    - **from_utc**: Only events created at or after this time (UTC)
    - **to_utc**: Only events created at or before this time (UTC)
    - **limit**: Maximum number of events to return (1-1000)
    - **skip**: Number of records to skip for pagination
    - **sort_order**: Sort direction for created_at
    ('asc' = ascending/oldest first, 'desc' = descending/newest first, default: 'desc')
    """
    # Validate event_type
    if event_type is not None and MetadataEventType.is_valid(event_type) is False:
        raise HTTPException(
            status_code=400, detail=f"Invalid event_type. Must be one of: {', '.join(MetadataEventType)}"
        )

    # Validate time range
    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise HTTPException(status_code=400, detail="from_utc must be before to_utc")

    # Validate sort_order
    if sort_order not in ["asc", "desc"]:
        raise HTTPException(status_code=400, detail="sort_order must be 'asc' (ascending) or 'desc' (descending)")

    try:
        pg_repo = PgTranscriptRepository()

        # Get events and count
        events = await pg_repo.get_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc,
            limit=limit,
            skip=skip,
            sort_order=sort_order,
        )

        total = await pg_repo.count_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc,
        )
        return {
            "status": "ok",
            "total": total,
            "limit": limit,
            "skip": skip,
            "ttl_seconds": 259200,  # 3 days in seconds
            "data": events,
        }

    except Exception as e:
        logger.error(f"Failed to list metadata events: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list metadata events: {e!s}") from e


@router.get("/metadata/{event_id}", response_description="Get metadata event by event_id")
async def get_metadata_event_by_id(
    event_id: str,
):
    """
    Get single metadata event by event_id (UUID).

    - **event_id**: Event UUID
    """
    try:
        pg_repo = PgTranscriptRepository()

        event = await pg_repo.get_metadata_event_by_event_id(event_id)

        if not event:
            raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

        return {"status": "ok", "data": event}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get metadata event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get metadata event: {e!s}") from e
