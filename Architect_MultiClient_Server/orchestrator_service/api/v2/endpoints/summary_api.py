"""
Internal API endpoints for room summary
"""
from fastapi import APIRouter, HTTPException, Query, Depends
from datetime import datetime
from typing import Optional
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.utils.logger import get_logger
logger = get_logger(__name__)

client_router = APIRouter(prefix="/api/summary", tags=["Summary"])

@client_router.get("/room/{room_name}", response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: str,
    start_time: Optional[datetime] = Query(None, description="Start time for room summary (ISO format: 2024-01-01T00:00:00)"),
    end_time: Optional[datetime] = Query(None, description="End time for room summary (ISO format: 2024-01-31T23:59:59)"),
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN))
):
    """
    Get summary by room name.
    """
    mongodb = MongoDBService()
    if not mongodb.connected:
        await mongodb.connect()

    if auth.can_view_all_rooms:
        summaries = await mongodb.get_summary_by_room_name(room_name, start_time, end_time)

    else:
        summaries = await mongodb.get_summary_by_room_name(room_name, start_time, end_time, auth.user_id)

    return {
        "status": "ok",
        "data": summaries,
        "count": len(summaries)
    }

@client_router.get("/room/id/{room_id}", response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN))
):
    """
    Get summary by room id.
    """
    mongodb = MongoDBService()
    if not mongodb.connected:
        await mongodb.connect()

    if not auth.can_view_all_rooms:
        has_access = await mongodb.user_has_room_access(room_id, auth.user_id)
        if not has_access:
            logger.warning(f"User {auth.user_id} denied access to room statistics for {room_id}")
            raise HTTPException(
                status_code=403,
                detail="You don't have access to this room"
            )

    summary = await mongodb.get_summary_by_room_id(room_id)
    return {
        "status": "ok",
        "data": summary
    }
