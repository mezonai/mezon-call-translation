"""
Room API endpoints for querying room data from PostgreSQL
- List rooms with filters and pagination
- Get room details by name
- Get room statistics
"""
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.models.room_models import (
    AudioInfoResponse,
    RoomDetailResponse,
    RoomIdPath,
    RoomListQuery,
    RoomListResponse,
    RoomStatisticResponse,
)
from orchestrator_service.services.room_service import RoomService, get_room_service
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.logger import get_logger

router = APIRouter(prefix="/rooms", tags=["Rooms"])
logger = get_logger(__name__)


@router.get("",response_model=RoomListResponse, response_description="List all rooms")
async def list_rooms(
    filters: Annotated[RoomListQuery, Query()],
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
) -> RoomListResponse:
    """
    List rooms based on user permissions:
    - Admin/Bot (rooms:view_all): See all rooms
    - User (rooms:view_own): See only participated rooms

    - **status**: Filter rooms by status (e.g. 'pending', 'completed')
    - **search**: Search by room name or participant identity (matches tracks)
    - **from_utc**: Only rooms created at or after this time (UTC)
    - **to_utc**: Only rooms created at or before this time (UTC)
    - **limit**: Maximum number of rooms to return
    - **skip**: Number of records to skip for pagination
    """
    rooms, total = await room_service.list_rooms(
        auth=auth,
        status=filters.status,
        search=filters.search,
        from_utc=filters.from_utc,
        to_utc=filters.to_utc,
        limit=filters.limit,
        skip=filters.skip,
    )
    return RoomListResponse(
        status="ok",
        total=total,
        limit=filters.limit,
        skip=filters.skip,
        rooms=rooms, # type: ignore[arg-type]
    )


@router.get("/id/{room_id}", response_model=RoomDetailResponse, response_description="Get room by ID")
async def get_room_by_id(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
) -> RoomDetailResponse:
    """
    Get room details by room ID.
    - Admin/Bot: Can access any room
    - User: Can only access participated rooms
    """
    room = await room_service.get_room_by_id(str(room_id), auth)
    return RoomDetailResponse(
        status="ok",
        room=room, # type: ignore[arg-type]
    )


@router.get("/id/{room_id}/statistics", response_model=RoomStatisticResponse, response_description="Get room statistics by ID")
async def get_room_statistics_by_id(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
) -> RoomStatisticResponse:
    """
    Get detailed statistics for a specific room by ID.
    - Admin/Bot: Can access any room
    - User: Can only access participated rooms

    Returns:
    - Total tracks, completed/remaining tracks
    - Total duration in seconds
    - Total transcript segments
    """
    stats = await room_service.get_room_statistics(str(room_id), auth)
    return RoomStatisticResponse(
        status="ok",
        statistics=stats, # type: ignore[arg-type]
    )


@router.get("/audio_info/{room_id}", response_model=AudioInfoResponse, response_description="Get all audio info for a room")
async def get_audio_info(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
) -> AudioInfoResponse:
    """
    Get all audio info for a specific room by ID.

    Returns:
    - List of audio files associated with the room
    """

    file_results = await room_service.get_audio_info(str(room_id), auth)
    return AudioInfoResponse(
        status="ok",
        file_results=file_results,
    )


@router.post("/id/{room_id}/retry-transcription", response_description="Retry transcription for all tracks of a room")
async def retry_room_transcription_endpoint(
    room_id: RoomIdPath,
) -> dict[str, Any]:
    """
    Test API: Trigger full transcription flow for a room by room_id.
    Flow:
    1. Resets previous chunks & summaries in DB to avoid duplicate data.
    2. Enqueues TranscriptionTask for each track into Redis 'transcription:stream'.
    3. stt-non-realtime worker downloads audio from MinIO and performs Whisper ASR.
    4. Segments are streamed back to Orchestrator and saved to DB.
    5. Meeting summary is automatically triggered upon completion of all tracks.
    """
    try:
        service = TranscriptionService()
        result = await service.retry_room_transcription(room_id=str(room_id))
        return {"status": "ok", **result}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Failed to retry room transcription: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e!s}") from e


