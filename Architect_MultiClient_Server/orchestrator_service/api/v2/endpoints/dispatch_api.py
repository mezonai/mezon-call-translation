from typing import Any, ClassVar

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from orchestrator_service.services.livekit_client import (
    LiveKitServiceError,
)
from orchestrator_service.services.room_service import RoomService, get_room_service

router = APIRouter()


class DispatchRequestModel(BaseModel):
    room_name: str = Field(..., description="Room name")

    class Config:
        json_schema_extra: ClassVar[dict] = {"example": {"room_name": "Interview Room 1"}}


class DispatchActionResponseModel(BaseModel):
    status: str
    message: str | None = None
    dispatch: dict[str, Any] | None = None


class ParticipantModel(BaseModel):
    identity: str
    name: str
    state: str
    joined_at: int
    metadata: dict[str, Any]


class ParticipantListResponseModel(BaseModel):
    status: str
    participants: list[ParticipantModel]


@router.post("/create_dispatch")
async def api_create_dispatch(
    body: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> DispatchActionResponseModel:
    """Create a dispatch for the specified room."""
    try:
        result = await room_service.create_dispatch(body.room_name)
        return DispatchActionResponseModel(**result)
    except HTTPException:
        raise
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.post("/cancel_dispatch")
async def api_cancel_dispatch(
    body: DispatchRequestModel,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> DispatchActionResponseModel:
    """Cancel a dispatch for the specified room."""
    try:
        result = await room_service.cancel_dispatch(body.room_name)
        return DispatchActionResponseModel(**result)
    except HTTPException:
        raise
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/rooms/participant/{room_id}", response_model=ParticipantListResponseModel)
async def list_participants(
    room_id: str,
    auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL)),
    room_service: RoomService = Depends(get_room_service),
) -> ParticipantListResponseModel:
    """List participants in a room."""
    try:
        participants_data = await room_service.list_participants(room_id)
        return ParticipantListResponseModel(
            status="ok", participants=[ParticipantModel(**participant) for participant in participants_data]
        )
    except HTTPException:
        raise
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
