from fastapi import APIRouter, Depends

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from orchestrator_service.models.dispatch_models import (
    DispatchRequestModel,
    RoomIdPath,
)
from orchestrator_service.services.livekit_client import (
    DispatchActionResponseModel,
    ParticipantListResponseModel,
    ParticipantModel,
)
from orchestrator_service.services.room_service import RoomService, get_room_service

router = APIRouter()


@router.post("/create_dispatch", response_model=DispatchActionResponseModel)
async def api_create_dispatch(
    body: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> DispatchActionResponseModel:
    """Create a dispatch for the specified room."""
    return await room_service.create_dispatch(body.room_name)


@router.post("/cancel_dispatch", response_model=DispatchActionResponseModel)
async def api_cancel_dispatch(
    body: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> DispatchActionResponseModel:
    """Cancel a dispatch for the specified room."""
    return await room_service.cancel_dispatch(body.room_name)


@router.get("/rooms/participant/{room_id}", response_model=ParticipantListResponseModel)
async def list_participants(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> ParticipantListResponseModel:
    """List participants in a room."""
    participants_data = await room_service.list_participants(str(room_id))

    return ParticipantListResponseModel(
        status="ok",
        participants=[
            ParticipantModel(**participant.model_dump())
            for participant in participants_data
        ],
    )
