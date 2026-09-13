from typing import ClassVar

from fastapi import APIRouter, Depends

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from orchestrator_service.models.dispatch_models import RoomIdPath
from orchestrator_service.models.room_models import ParticipantListResponseModel
from orchestrator_service.services.room_service import (
    RoomService,
    get_room_service,
)

router = APIRouter()


class DispatchRequestModel(BaseModel):  # type: ignore[explicit-any]
    room_name: str = Field(..., description="Room name")

    class Config:
        json_schema_extra: ClassVar[dict[str, dict[str, str]]] = {"example": {"room_name": "Interview Room 1"}}

@router.post("/create_dispatch")
async def api_create_dispatch(
    _: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))
) -> dict[str,str]:
    """Create a dispatch for the specified room."""
    return {"status": "ok"}


@router.post("/cancel_dispatch")
async def api_cancel_dispatch(
    _: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))
) -> dict[str,str]:
    """Cancel a dispatch for the specified room."""
    return {"status": "ok"}

@router.get(
    "/rooms/participant/{room_id}",
    response_model=ParticipantListResponseModel,
)
async def list_participants(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> ParticipantListResponseModel:
    """List participants recorded for a room."""
    participants = await room_service.list_participants(str(room_id))

    return ParticipantListResponseModel(
        status="ok",
        participants=participants,
    )
