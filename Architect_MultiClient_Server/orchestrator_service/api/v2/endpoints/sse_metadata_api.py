"""
SSE Metadata API
Endpoints for bot to receive agent metadata events via SSE
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from uuid import UUID
from pydantic import BaseModel, Field, field_validator
from typing import Optional, Dict, Any
from datetime import datetime

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import METADATA_EVENTS_VIEW_ALL
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.sse_metadata_service import SseMetadataService, get_sse_metadata_service
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.metadata_event_models import MetadataEventType


logger = get_logger(__name__)
router = APIRouter()


# ==================== Pydantic Models ====================

class RoomInfo(BaseModel):
    """Room information"""
    room_id: str = Field(..., description="Room identifier")
    room_name: str = Field(..., description="Room name")

    @field_validator("room_id")
    @classmethod
    def validate_uuid(cls, v: str) -> str:
        try:
            UUID(v)
        except (ValueError, AttributeError):
            raise ValueError(f"Invalid UUID format: {v!r}")
        return v

    class Config:
        json_schema_extra = {
            "example": {
                "room_id": "abc123",
                "room_name": "Interview Room 1"
            }
        }


class SessionStartedRequest(RoomInfo):
    """Request model for session_started event"""
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": "abc123",
                "room_name": "Interview Room 1"
            }
        }


class SessionEndedRequest(RoomInfo):
    """Request model for session_ended event"""
    duration_seconds: Optional[int] = Field(None, description="Duration of room session in seconds")
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": "abc123",
                "room_name": "Interview Room 1",
                "duration_seconds": 3600
            }
        }


class FileResult(BaseModel):
    """Recording file result"""
    participant_identity: str = Field(..., description="Identity of participant")
    filename: str = Field(..., description="Name of the recording file")
    start_time: str = Field(..., description="Recording start time (ISO 8601)")
    end_time: str = Field(..., description="Recording end time (ISO 8601)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "participant_identity": "user_1",
                "filename": "user_1_audio.mp3",
                "start_time": "2026-03-02T10:00:01Z",
                "end_time": "2026-03-02T11:00:00Z"
            }
        }


class SessionRecordDoneRequest(RoomInfo):
    """Request model for room_record_done event"""
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": "abc123",
                "room_name": "Room_1",
            }
        }


class SessionSummaryDoneRequest(RoomInfo):
    """Request model for room_summary_done event"""
    class Config:
        json_schema_extra = {
            "example": {
                "room_id": "69a66008cfc00881f1d7b382",
                "room_name": "H3U-EXdDg"
            }
        }


# ==================== SSE Endpoint ====================

@router.get("/sse/metadata")
async def sse_metadata_endpoint(
    auth: AuthContext = Depends(require_any_permission(METADATA_EVENTS_VIEW_ALL)),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    SSE endpoint for bot to receive agent metadata events.
    
    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token
    
    Returns:
        StreamingResponse with SSE events
    """
    return await sse_service.create_connection(auth.user_id)


# ==================== Push Endpoints ====================

@router.post("/push_metadata/session_started")
async def push_session_started_api(
    req: SessionStartedRequest, 
    auth: Dict[str, Any] = Depends(verify_api_key),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    Push session_started event to all connected bots via SSE.
    
    Args:
        req: Session started event data
    
    Returns:
        Status and statistics
        
    """
    return await sse_service.push_room_started(
        room_id=req.room_id,
        room_name=req.room_name
    )


@router.post("/push_metadata/session_ended")
async def push_session_ended_api(
    req: SessionEndedRequest, 
    auth: Dict[str, Any] = Depends(verify_api_key),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    Push session_ended event to all connected bots via SSE.
    
    Args:
        req: Session ended event data
    
    Returns:
        Status and statistics
    
    """
    return await sse_service.push_room_ended(
        room_id=req.room_id,
        room_name=req.room_name,
        duration_seconds=req.duration_seconds
    )


@router.post("/push_metadata/session_record_done")
async def push_session_record_done_api(
    req: SessionRecordDoneRequest, 
    auth: Dict[str, Any] = Depends(verify_api_key),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    Push session_record_done event to all connected bots via SSE.
    File results are automatically fetched from PostgreSQL based on room_id.
    
    Args:
        req: Session record done event data
    
    Returns:
        Status and statistics

    """
    return await sse_service.push_room_record_done(
        room_id=req.room_id,
        room_name=req.room_name
    )


@router.post("/push_metadata/session_summary_done")
async def push_session_summary_done_api(
    req: SessionSummaryDoneRequest, 
    auth: Dict[str, Any] = Depends(verify_api_key),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    Push session_summary_done event to all connected bots via SSE.
    Notifies that room summary/analysis has been completed.
    
    Args:
        req: Session summary done event data
    
    Returns:
        Status and statistics
    
    """
    return await sse_service.push_room_summary_done(
        room_id=req.room_id,
        room_name=req.room_name
    )

@router.get("/metadata", response_description="List metadata events")
async def list_metadata_events(
    event_type: Optional[str] = Query(None, description="Filter by event type (room_started, room_ended, room_record_done, room_summary_done)"),
    room_id: Optional[str] = Query(None, description="Filter by room ID"),
    from_utc: Optional[datetime] = Query(None, description="Start of time range (UTC, ISO 8601)"),
    to_utc: Optional[datetime] = Query(None, description="End of time range (UTC, ISO 8601)"),
    limit: int = Query(100, ge=1, le=1000, description="Max records to return"),
    skip: int = Query(0, ge=0, description="Number of records to skip"),
    sort_order: str = Query("desc", description="Sort order: 'asc' for ascending, 'desc' for descending (default: 'desc')"),
    auth: AuthContext = Depends(require_any_permission(METADATA_EVENTS_VIEW_ALL)),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
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
    - **sort_order**: Sort direction for created_at ('asc' = ascending/oldest first, 'desc' = descending/newest first, default: 'desc')
    """
    # Validate event_type
    if event_type is not None and MetadataEventType.is_valid(event_type) is False:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid event_type. Must be one of: {', '.join(MetadataEventType)}"
        )

    # Validate time range
    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise HTTPException(status_code=400, detail="from_utc must be before to_utc")

    # Validate sort_order
    if sort_order not in ["asc", "desc"]:
        raise HTTPException(status_code=400, detail="sort_order must be 'asc' (ascending) or 'desc' (descending)")
    
    try:
        events, total = await sse_service.list_metadata_events(
            event_type, room_id, from_utc, to_utc, limit, skip, sort_order
        )
        return {
            "status": "ok",
            "total": total,
            "limit": limit,
            "skip": skip,
            "ttl_seconds": 259200,  # 3 days
            "data": events
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list metadata events: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list metadata events: {str(e)}")


@router.get("/metadata/{event_id}", response_description="Get metadata event by event_id")
async def get_metadata_event_by_id(
    event_id: str,
    auth: AuthContext = Depends(require_any_permission(METADATA_EVENTS_VIEW_ALL)),
    sse_service: SseMetadataService = Depends(get_sse_metadata_service)
):
    """
    Get single metadata event by event_id (UUID).

    - **event_id**: Event UUID
    """
    try:
        event = await sse_service.get_metadata_event_by_id(event_id)
        return {
            "status": "ok",
            "data": event
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get metadata event: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get metadata event: {str(e)}")
