"""
Internal API endpoints for room summary
"""

from fastapi import APIRouter, Depends, Query

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL, ROOMS_VIEW_OWN
from orchestrator_service.models.room_models import RoomIdPath
from orchestrator_service.models.summary_models import (
    SummaryDetailResponse,
    SummaryListQuery,
    SummaryListResponse,
)
from orchestrator_service.services.summary_service import SummaryService, get_summary_service
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.transcript_validators import RoomNamePath

logger = get_logger(__name__)

client_router = APIRouter(prefix="/summary", tags=["Summary"])


@client_router.get("/room/{room_name}", response_model=SummaryListResponse, response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: RoomNamePath,
    query: SummaryListQuery = Query(),
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    summary_service: SummaryService = Depends(get_summary_service),
) -> SummaryListResponse:
    """
    Get summary by room name.
    """
    data, count = await summary_service.get_summary_by_room_name(room_name, query.start_time, query.end_time, auth)
    return SummaryListResponse(status="ok", data=data, count=count)


@client_router.get("/room/id/{room_id}", response_model=SummaryDetailResponse, response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL, ROOMS_VIEW_OWN)),
    summary_service: SummaryService = Depends(get_summary_service),
) -> SummaryDetailResponse:
    """
    Get summary by room id.
    """
    summary = await summary_service.get_summary_by_room_id(str(room_id), auth)
    return SummaryDetailResponse(status="ok", data=summary)
