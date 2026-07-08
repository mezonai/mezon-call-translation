"""
Room API endpoints for querying room data from PostgreSQL
- List rooms with filters and pagination
- Get room details by name
- Get room statistics
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.services.livekit_client import AudioTrackInfo
from orchestrator_service.services.room_service import RoomService, get_room_service
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.transcript_validators import (
    LimitQuery,
    SkipQuery,
    StatusQuery,
)

router = APIRouter(prefix="/rooms", tags=["Rooms"])
logger = get_logger(__name__)


@router.get("", response_description="List all rooms")
async def list_rooms(
    status: StatusQuery = None,
    search: str | None = Query(
        default=None,
        max_length=VC.MAX_SEARCH_QUERY_LENGTH,
        description="Search by room name or participant identity",
    ),
    from_utc: datetime | None = Query(default=None, description="Start of time range (UTC, ISO 8601)"),
    to_utc: datetime | None = Query(default=None, description="End of time range (UTC, ISO 8601)"),
    limit: LimitQuery = VC.DEFAULT_LIMIT,
    skip: SkipQuery = VC.DEFAULT_SKIP,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
):
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
    if from_utc is not None and to_utc is not None and from_utc >= to_utc:
        raise HTTPException(status_code=400, detail="from_utc must be before to_utc")

    search_trimmed = search.strip() if search else None
    if search_trimmed == "":
        search_trimmed = None

    try:
        rooms, total = await room_service.list_rooms(
            auth=auth,
            status=status,
            search=search_trimmed,
            from_utc=from_utc,
            to_utc=to_utc,
            limit=limit,
            skip=skip,
        )

        return {
            "status": "ok",
            "total": total,
            "limit": limit,
            "skip": skip,
            "rooms": rooms,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list rooms: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/id/{room_id}", response_description="Get room by ID")
async def get_room_by_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
):
    """
    Get room details by room ID.
    - Admin/Bot: Can access any room
    - User: Can only access participated rooms
    """
    try:
        room = await room_service.get_room_by_id(room_id, auth)
        return {"status": "ok", "room": room}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/id/{room_id}/statistics", response_description="Get room statistics by ID")
async def get_room_statistics_by_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
):
    """
    Get detailed statistics for a specific room by ID.
    - Admin/Bot: Can access any room
    - User: Can only access participated rooms

    Returns:
    - Total tracks, completed/remaining tracks
    - Total duration in seconds
    - Total transcript segments
    """
    try:
        stats = await room_service.get_room_statistics(room_id, auth)
        return {"status": "ok", "statistics": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/audio_info/{room_id}", response_description="Get all audio info for a room")
async def get_audio_info(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    room_service: RoomService = Depends(get_room_service),
) -> dict[str, str | list[AudioTrackInfo]]:
    """
    Get all audio info for a specific room by ID.

    Returns:
    - List of audio files associated with the room
    """

    try:
        file_results = await room_service.get_audio_info(room_id, auth)
        return {"status": "ok", "file_results": file_results}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"[Metadata Channel] Failed to fetch tracks for room {room_id}: {e}")
        return {
            "status": "error",
            "message": f"Failed to fetch audio info for room {room_id}: {e!s}",
        }
