"""
Room API endpoints for querying room data from MongoDB
- List rooms with filters and pagination
- Get room details by name
- Get room statistics
"""

from datetime import datetime
from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.mongodb_service import get_mongodb_service
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.utils.transcript_validators import (
    RoomNamePath,
    StatusQuery,
    LimitQuery,
    SkipQuery,
    validate_date_range
)
from bson import ObjectId

router = APIRouter(prefix="/api/transcripts/rooms", tags=["Rooms"])
logger = get_logger(__name__)


@router.get("", response_description="List all rooms")
async def list_rooms(
    status: StatusQuery = None,
    limit: LimitQuery = VC.DEFAULT_LIMIT,
    skip: SkipQuery = VC.DEFAULT_SKIP,
):
    """
    List all rooms with optional filters.
    
    - **status**: Filter rooms by status (e.g., 'processing', 'completed')
    - **limit**: Maximum number of rooms to return
    - **skip**: Number of records to skip for pagination
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        # Query without date range (standard list with pagination)
        rooms = await mongodb.list_rooms(status=status, limit=limit, skip=skip)
        total = await mongodb.count_rooms_by_status(status)
        return {
            "status": "ok",
            "total": total,
            "limit": limit,
            "skip": skip,
            "rooms": rooms
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to list rooms: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/id/{room_id}", response_description="Get room by ID")
async def get_room_by_id(
    room_id: str,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get room details by room ID.
    
    - **room_id**: The ObjectId of the room to retrieve
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        # Validate ObjectId format
        try:
            ObjectId(room_id)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid room_id format: '{room_id}'")
        
        room = await mongodb.get_room_by_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")
        
        room["_id"] = str(room["_id"])
        
        return {
            "status": "ok",
            "room": room
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/id/{room_id}/statistics", response_description="Get room statistics by ID")
async def get_room_statistics_by_id(
    room_id: str,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get detailed statistics for a specific room by ID.
    
    - **room_id**: The ObjectId of the room
    
    Returns:
    - Total tracks, completed/remaining tracks
    - Total duration in seconds
    - Total transcript segments
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        # Validate ObjectId format
        try:
            ObjectId(room_id)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid room_id format: '{room_id}'")
        
        stats = await mongodb.get_room_statistics_by_id(room_id)
        if not stats:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")
        
        return {
            "status": "ok",
            "statistics": stats
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get room statistics: {e}")
        raise HTTPException(status_code=500, detail=str(e))
