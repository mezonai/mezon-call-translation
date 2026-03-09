"""
SSE Metadata API
Endpoints for bot to receive agent metadata events via SSE
"""
from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional, List

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Get singleton instances
metadata_channel = MetadataChannel()


# ==================== Pydantic Models ====================

class RoomInfo(BaseModel):
    """Room information"""
    room_id: str = Field(..., description="Room identifier")
    room_name: str = Field(..., description="Room name")
    
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
async def sse_metadata_endpoint(appid: str, token: str):
    """
    SSE endpoint for bot to receive agent metadata events.
    
    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token
    
    Returns:
        StreamingResponse with SSE events
    """
    return await metadata_channel.create_connection(appid, token)


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
    result = await metadata_channel.push_room_started(
        room_id=req.room_id,
        room_name=req.room_name
    )
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
        room_id=req.room_id,
        room_name=req.room_name,
        duration_seconds=req.duration_seconds
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
    result = await metadata_channel.push_room_record_done(
        room_id=req.room_id,
        room_name=req.room_name
    )
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
    result = await metadata_channel.push_room_summary_done(
        room_id=req.room_id,
        room_name=req.room_name
    )
    return result
