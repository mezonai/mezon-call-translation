"""
Internal API endpoints for room summary
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.services.summary_service import SummaryService, get_summary_service
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

client_router = APIRouter(prefix="/summary", tags=["Summary"])


@client_router.get("/room/{room_name}", response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: str,
    start_time: datetime | None = Query(
        None,
        description="Start time for room summary (ISO format: 2024-01-01T00:00:00)",
    ),
    end_time: datetime | None = Query(None, description="End time for room summary (ISO format: 2024-01-31T23:59:59)"),
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    summary_service: SummaryService = Depends(get_summary_service),
):
    """
    Get summary by room name.
    """
    try:
        data, count = await summary_service.get_summary_by_room_name(room_name, start_time, end_time, auth)
        return {"status": "ok", "data": data, "count": count}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get summary by room name {room_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@client_router.get("/room/id/{room_id}", response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    summary_service: SummaryService = Depends(get_summary_service),
):
    """
    Get summary by room id.
    """
    try:
        summary = await summary_service.get_summary_by_room_id(room_id, auth)
        return {"status": "ok", "data": summary}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get summary by room id {room_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
