from fastapi import APIRouter, Depends

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from orchestrator_service.models.dispatch_models import RoomIdPath
from orchestrator_service.models.room_models import ParticipantListResponseModel

router = APIRouter()


@router.get("/rooms/participant/{room_id}", response_model=ParticipantListResponseModel)
async def list_participants(
    room_id: RoomIdPath,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
) -> ParticipantListResponseModel:
    """List participants in a room."""
    # TODO: implement this endpoint
    return ParticipantListResponseModel(
        status="ok",
        participants=[],
    )
