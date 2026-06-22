"""
Room API endpoints for querying room data from MongoDB
- List rooms with filters and pagination
- Get room details by name
- Get room statistics
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.services.postgresql.models import Room
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
)
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601
from orchestrator_service.utils.transcript_validators import (
    LimitQuery,
    SkipQuery,
    StatusQuery,
)

router = APIRouter(prefix="/api/transcripts/rooms", tags=["Rooms"])
logger = get_logger(__name__)


def _serialize_room(room: Room) -> dict:
    serialized_room = {
        "id": room.id,
        "room_name": room.room_name,
        "status": room.status,
        "participants": room.participants,
        "created_at": room.created_at,
        "finalized_at": room.finalized_at,
        "completed_at": room.completed_at,
    }
    return serialized_room


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
):
    """
    List all rooms with optional filters.

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
):
    """
    Get room details by room ID.
    """
    try:
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        room = await pg_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")

        room = _serialize_room(room)

        return {"status": "ok", "room": room}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/id/{room_id}/statistics", response_description="Get room statistics by ID")
async def get_room_statistics_by_id(
    room_id: str,
):
    """
    Get detailed statistics for a specific room by ID.

    Returns:
    - Total tracks, completed/remaining tracks
    - Total duration in seconds
    - Total transcript segments
    """
    try:
        pg_repo = PgTranscriptRepository()
        if not pg_repo.connected:
            await pg_repo.connect()

        summary, room = await pg_repo.get_summary_by_room_id(room_id)
        tracks = await pg_repo.get_tracks_by_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")

        total_segments = summary.total_segments if summary else 0
        total_tracks = len(tracks)
        completed_tracks = sum(1 for t in tracks if t.status == "completed")

        total_duration_sec: float = 0.0
        if room.finalized_at and room.created_at:
            total_duration_sec = (room.finalized_at - room.created_at).total_seconds()

        stats = {
            "room_id": room.id,
            "room_name": room.room_name,
            "status": room.status,
            "total_tracks": total_tracks,
            "completed_tracks": completed_tracks,
            "remaining_tracks": total_tracks - completed_tracks,
            "total_segments": total_segments,
            "created_at": convert_to_iso_8601(room.created_at) if room.created_at else None,
            "finalized_at": convert_to_iso_8601(room.finalized_at) if room.finalized_at else None,
            "total_duration_sec": total_duration_sec,
        }

        return {"status": "ok", "statistics": stats}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e
