"""
SSE Metadata API
Endpoints for bot to receive agent metadata events via SSE
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.models.sse_metadata_models import (
    MetadataEventDetailResponse,
    MetadataEventIdPath,
    MetadataEventListResponse,
    MetadataListQuery,
    MetadataPushResponse,
    SessionEndedRequest,
    SessionRecordDoneRequest,
    SessionStartedRequest,
    SessionSummaryDoneRequest,
)
from orchestrator_service.services.sse_metadata_service import (
    SseMetadataService,
    get_sse_metadata_service,
)

router = APIRouter()

# Get singleton instances
metadata_channel = MetadataChannel()


@router.get("/sse/metadata", response_class=StreamingResponse)
async def sse_metadata_endpoint(
    appid: Annotated[str, Query(min_length=1, description="Application ID")],
    token: Annotated[str, Query(min_length=1, description="Account authentication token")],
) -> StreamingResponse:
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
        raise HTTPException(status_code=401, detail="Account authentication failed")

    return await metadata_channel.create_connection(appid)


# ==================== Push Endpoints ====================


@router.post("/push_metadata/session_started", response_model=MetadataPushResponse )
async def push_session_started_api(req: SessionStartedRequest) -> MetadataPushResponse:
    """
    Push session_started event to all connected bots via SSE.

    Args:
        req: Session started event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_started(room_id=req.room_id, room_name=req.room_name)
    return MetadataPushResponse.model_validate(result)


@router.post("/push_metadata/session_ended", response_model=MetadataPushResponse)
async def push_session_ended_api(req: SessionEndedRequest) -> MetadataPushResponse:
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
    return MetadataPushResponse.model_validate(result)


@router.post("/push_metadata/session_record_done", response_model=MetadataPushResponse)
async def push_session_record_done_api(req: SessionRecordDoneRequest) -> MetadataPushResponse:
    """
    Push session_record_done event to all connected bots via SSE.
    File results are automatically fetched from MongoDB based on room_id.

    Args:
        req: Session record done event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_record_done(room_id=req.room_id, room_name=req.room_name)
    return MetadataPushResponse.model_validate(result)


@router.post("/push_metadata/session_summary_done", response_model=MetadataPushResponse)
async def push_session_summary_done_api(req: SessionSummaryDoneRequest) -> MetadataPushResponse:
    """
    Push session_summary_done event to all connected bots via SSE.
    Notifies that room summary/analysis has been completed.

    Args:
        req: Session summary done event data

    Returns:
        Status and statistics

    """
    result = await metadata_channel.push_room_summary_done(room_id=req.room_id, room_name=req.room_name)
    return MetadataPushResponse.model_validate(result)


@router.get("/metadata", response_model=MetadataEventListResponse, response_description="List metadata events")
async def list_metadata_events(
    filters: Annotated[MetadataListQuery, Query()],
    sse_service: SseMetadataService = Depends(get_sse_metadata_service),
) -> MetadataEventListResponse:
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

    # Get events and count
    events, total = await sse_service.list_metadata_events(
        event_type=filters.event_type,
        room_id=filters.room_id,
        from_utc=filters.from_utc,
        to_utc=filters.to_utc,
        limit=filters.limit,
        skip=filters.skip,
        sort_order=filters.sort_order,
    )

    return MetadataEventListResponse(
        status="ok",
        total=total,
        limit=filters.limit,
        skip=filters.skip,
        ttl_seconds=259200,  # 3 days in seconds
        data=events,
    )



@router.get("/metadata/{event_id}", response_model=MetadataEventDetailResponse, response_description="Get metadata event by event_id")
async def get_metadata_event_by_id(
    event_id: MetadataEventIdPath,
    sse_service: SseMetadataService = Depends(get_sse_metadata_service),
) -> MetadataEventDetailResponse:
    """
    Get single metadata event by event_id (UUID).

    - **event_id**: Event UUID
    """

    event = await sse_service.get_metadata_event_by_id(str(event_id))

    if not event:
        raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

    return MetadataEventDetailResponse(
        status="ok",
        data=event,
    )

