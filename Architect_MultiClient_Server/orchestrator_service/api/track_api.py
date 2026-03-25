"""
Track API endpoints for querying track data from MongoDB
- List tracks with filters and pagination
- Get track details by egress ID or ObjectId
- Get tracks by room or participant
- Get participant statistics
"""

from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.utils.transcript_validators import (
    RoomNamePath,
    TrackIdPath,
    EgressIdPath,
    ParticipantIdentityPath,
    StatusQuery,
    LimitQuery,
    SkipQuery,
    validate_date_range
)

router = APIRouter(prefix="/api/transcripts/tracks", tags=["Tracks"])
logger = get_logger(__name__)


@router.get("", response_description="List all tracks")
async def list_tracks(
    status: StatusQuery = None,
    limit: LimitQuery = VC.DEFAULT_LIMIT,
    skip: SkipQuery = VC.DEFAULT_SKIP,
    start_date: Optional[datetime] = Query(None, description="Start date for date range filter (ISO format: 2024-01-01T00:00:00)"),
    end_date: Optional[datetime] = Query(None, description="End date for date range filter (ISO format: 2024-01-31T23:59:59)"),
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    List all tracks with optional filters.
    
    - **status**: Filter tracks by status
    - **limit**: Maximum number of tracks to return
    - **skip**: Number of records to skip for pagination
    - **start_date**: Start of date range filter (ISO format, requires end_date)
    - **end_date**: End of date range filter (ISO format, requires start_date)
    
    Note: Pagination (limit/skip) works with or without date range filter.
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        # Validate date range if provided
        if (start_date is not None) != (end_date is not None):
            raise HTTPException(
                status_code=400,
                detail="Both start_date and end_date must be provided together"
            )
        
        if start_date is not None and end_date is not None:
            # Validate start_date < end_date
            validate_date_range(start_date, end_date)
            
            # Query by date range with pagination
            tracks = await mongodb.get_tracks_by_date_range(
                start_date, end_date, status=status, limit=limit, skip=skip
            )
            total = await mongodb.count_tracks_by_date_range(start_date, end_date, status=status)
            
            # Convert ObjectId to string for JSON serialization
            for track in tracks:
                track["_id"] = str(track["_id"])
                if "room_ref_id" in track:
                    track["room_ref_id"] = str(track["room_ref_id"])
            
            return {
                "status": "ok",
                "total": total,
                "limit": limit,
                "skip": skip,
                "date_range": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat()
                },
                "tracks": tracks
            }
        else:
            # Query without date range (standard list with pagination)
            tracks = await mongodb.list_tracks(status=status, limit=limit, skip=skip)
            
            # Convert ObjectId to string for JSON serialization
            for track in tracks:
                track["_id"] = str(track["_id"])
                if "room_ref_id" in track:
                    track["room_ref_id"] = str(track["room_ref_id"])
            
            return {
                "status": "ok",
                "limit": limit,
                "skip": skip,
                "tracks": tracks
            }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list tracks: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/egress/{egress_id}", response_description="Get track by egress ID")
async def get_track_by_egress_id(
    egress_id: EgressIdPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get track details by egress ID.
    
    - **egress_id**: The egress ID of the track
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        track = await mongodb.get_track_by_egress_id(egress_id)
        if not track:
            raise HTTPException(status_code=404, detail=f"Track with egress_id '{egress_id}' not found")
        
        track["_id"] = str(track["_id"])
        if "room_ref_id" in track:
            track["room_ref_id"] = str(track["room_ref_id"])
        
        return {
            "status": "ok",
            "track": track
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get track: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/id/{track_id}", response_description="Get track by ObjectId")
async def get_track_by_id(
    track_id: TrackIdPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get track details by ObjectId.
    
    - **track_id**: The MongoDB ObjectId of the track
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        track = await mongodb.get_track_by_id(track_id)
        if not track:
            raise HTTPException(status_code=404, detail=f"Track with id '{track_id}' not found")
        
        track["_id"] = str(track["_id"])
        if "room_ref_id" in track:
            track["room_ref_id"] = str(track["room_ref_id"])
        
        return {
            "status": "ok",
            "track": track
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get track by ID: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/room/{room_name}", response_description="Get tracks by room")
async def get_tracks_by_room(
    room_name: RoomNamePath,
    status: StatusQuery = None,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get all tracks for a specific room.
    
    - **room_name**: The name of the room
    - **status**: Optional filter by track status
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        # First get the room to get its ID
        room = await mongodb.get_room_by_name(room_name)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room '{room_name}' not found")
        
        room_id = str(room["_id"])
        tracks = await mongodb.get_tracks_by_room(room_id, status=status)
        count = await mongodb.count_tracks_by_room(room_id, status=status)
        
        for track in tracks:
            track["_id"] = str(track["_id"])
            if "room_ref_id" in track:
                track["room_ref_id"] = str(track["room_ref_id"])
        
        return {
            "status": "ok",
            "room_name": room_name,
            "total_tracks": count,
            "tracks": tracks
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get tracks by room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/participant/{participant_identity}", response_description="Get tracks by participant")
async def get_tracks_by_participant(
    participant_identity: ParticipantIdentityPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get all tracks for a specific participant.
    
    - **participant_identity**: The identity of the participant
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        tracks = await mongodb.get_tracks_by_participant(participant_identity)
        
        for track in tracks:
            track["_id"] = str(track["_id"])
            if "room_ref_id" in track:
                track["room_ref_id"] = str(track["room_ref_id"])
        
        return {
            "status": "ok",
            "participant_identity": participant_identity,
            "total_tracks": len(tracks),
            "tracks": tracks
        }
    except Exception as e:
        logger.error(f"Failed to get tracks by participant: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/participant/{participant_identity}/statistics", response_description="Get participant statistics")
async def get_participant_statistics(
    participant_identity: ParticipantIdentityPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get statistics for a participant across all rooms.
    
    - **participant_identity**: The identity of the participant
    
    Returns:
    - Total tracks
    - Unique rooms participated
    - Total duration in seconds
    - Total transcript segments
    """
    try:
        mongodb = MongoDBService()
        if not mongodb.connected:
            await mongodb.connect()
        
        stats = await mongodb.get_participant_statistics(participant_identity)
        
        return {
            "status": "ok",
            "statistics": stats
        }
    except Exception as e:
        logger.error(f"Failed to get participant statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
