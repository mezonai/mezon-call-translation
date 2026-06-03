"""
Room API endpoints for querying room data from PostgreSQL
- List rooms with filters and pagination
- Get room details by name
- Get room statistics
"""

from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, HTTPException, Query, Depends

from orchestrator_service.auth.authorization import require_any_permission, AuthContext
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
)
from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.utils.transcript_validators import (
    StatusQuery,
    LimitQuery,
    SkipQuery,
)

router = APIRouter(prefix="/rooms", tags=["Rooms"])
logger = get_logger(__name__)


def _serialize_room(room: dict) -> dict:
    serialized_room = dict(room)
    if serialized_room.get("_id") is not None:
        serialized_room["_id"] = str(serialized_room["_id"])
    return serialized_room


@router.get("", response_description="List all rooms")
async def list_rooms(
    status: StatusQuery = None,
    search: Optional[str] = Query(
        default=None,
        max_length=VC.MAX_SEARCH_QUERY_LENGTH,
        description="Search by room name or participant identity",
    ),
    from_utc: Optional[datetime] = Query(
        default=None, description="Start of time range (UTC, ISO 8601)"
    ),
    to_utc: Optional[datetime] = Query(
        default=None, description="End of time range (UTC, ISO 8601)"
    ),
    limit: LimitQuery = VC.DEFAULT_LIMIT,
    skip: SkipQuery = VC.DEFAULT_SKIP,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
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
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        # Check which permission user has
        if auth.can_view_all_rooms:
            # Admin/Bot - see all rooms
            rooms = await pg_repo.list_rooms(
                status=status,
                search=search_trimmed,
                from_utc=from_utc,
                to_utc=to_utc,
                limit=limit,
                skip=skip,
            )
            rooms = [_serialize_room(room) for room in rooms]
            total = await pg_repo.count_rooms(
                status=status,
                search=search_trimmed,
                from_utc=from_utc,
                to_utc=to_utc,
            )
        else:
            # Regular user - filter by participation
            rooms = await pg_repo.list_rooms_by_user(
                user_id=auth.user_id,
                status=status,
                search=search_trimmed,
                from_utc=from_utc,
                to_utc=to_utc,
                limit=limit,
                skip=skip,
            )
            rooms = [_serialize_room(room) for room in rooms]
            total = await pg_repo.count_rooms_by_user(
                user_id=auth.user_id,
                status=status,
                search=search_trimmed,
                from_utc=from_utc,
                to_utc=to_utc,
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
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/id/{room_id}", response_description="Get room by ID")
async def get_room_by_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
):
    """
    Get room details by room ID.
    - Admin/Bot: Can access any room
    - User: Can only access participated rooms
    """
    try:
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        if not auth.can_view_all_rooms:
            # User must have participated in this room
            has_access = await pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room {room_id}")
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        room = await pg_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(
                status_code=404, detail=f"Room with ID '{room_id}' not found"
            )

        # Check access permission for regular users

        room = _serialize_room(room)

        return {"status": "ok", "room": room}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/id/{room_id}/statistics", response_description="Get room statistics by ID"
)
async def get_room_statistics_by_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
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
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        # Check access permission for regular users
        if not auth.can_view_all_rooms:
            # User must have participated in this room
            has_access = await pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        stats = await pg_repo.get_room_statistics_by_id(room_id)
        if not stats:
            raise HTTPException(
                status_code=404, detail=f"Room with ID '{room_id}' not found"
            )

        if stats.get("room_id") is not None:
            stats["room_id"] = str(stats["room_id"])

        return {"status": "ok", "statistics": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get(
    "/audio_info/{room_id}", response_description="Get all audio info for a room"
)
async def get_audio_info(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
) -> dict[str, Any]:
    """
    Get all audio info for a specific room by ID.

    Returns:
    - List of audio files associated with the room
    """
    try:
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        # Check access permission for regular users
        if not auth.can_view_all_rooms:
            # User must have participated in this room
            has_access = await pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )
        
        file_results = []
        try:
            tracks = await pg_repo.get_tracks_by_room(room_id)
            if not tracks:
                raise HTTPException(
                    status_code=404,
                    detail=f"No tracks found for room with ID '{room_id}'",
                )
            for track in tracks:
                audio_info = track.get("audio_info") or {}
                started_at_ns = audio_info.get("started_at_ns")
                ended_at_ns = audio_info.get("ended_at_ns")

                file_result = {
                    "participant_identity": track.get("participant_identity", ""),
                    "filename": audio_info.get("filename", ""),
                    "started_at_ns": started_at_ns,
                    "ended_at_ns": ended_at_ns,
                }
                file_results.append(file_result)
            return {"status": "ok", "file_results": file_results}
        except Exception as e:
            logger.error(
                f"[Metadata Channel] Failed to fetch tracks for room {room_id}: {e}"
            )
            return {
                "status": "error",
                "message": f"Failed to fetch audio info for room {room_id}: {str(e)}",
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get audio info: {e}")
        raise HTTPException(status_code=500, detail=str(e))
