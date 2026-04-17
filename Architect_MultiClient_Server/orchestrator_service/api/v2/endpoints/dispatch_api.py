from google.protobuf.json_format import MessageToDict
from typing import Dict, Any, Optional, List

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.constants.permissions import AGENT_CONTROL
from fastapi import APIRouter, HTTPException, Depends
from bson import ObjectId
from pydantic import BaseModel, Field
from orchestrator_service.services.livekit_client import get_livekit_service, LiveKitServiceError
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService

router = APIRouter()


class DispatchRequestModel(BaseModel):
    room_name: str = Field(..., description="Room name")
    class Config:
        json_schema_extra = {
            "example": {
                "room_name": "Interview Room 1"
            }
        }


class DispatchActionResponseModel(BaseModel):
    status: str
    message: Optional[str] = None
    dispatch: Optional[Dict[str, Any]] = None


class ParticipantModel(BaseModel):
    identity: str
    name: str
    state: str
    joined_at: int
    metadata: dict[str, Any]


class ParticipantListResponseModel(BaseModel):
    status: str
    participants: List[ParticipantModel]


class DispatchActionResponseModel(BaseModel):
    status: str
    message: Optional[str] = None
    dispatch: Optional[Dict[str, Any]] = None


class ParticipantModel(BaseModel):
    identity: str
    name: str
    state: str
    joined_at: int
    metadata: dict[str, Any]


class ParticipantListResponseModel(BaseModel):
    status: str
    participants: List[ParticipantModel]


@router.post("/create_dispatch")
async def api_create_dispatch(body: DispatchRequestModel, auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))) -> DispatchActionResponseModel:
    """Create a dispatch for the specified room."""
    livekit_service = get_livekit_service()

    try:
        result = await livekit_service.ensure_dispatch(body.room_name)
        if result.get("dispatch") is not None:
            result["dispatch"] = MessageToDict(result["dispatch"], preserving_proto_field_name=True)
        return DispatchActionResponseModel(**result)
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel_dispatch")
async def api_cancel_dispatch(body: DispatchRequestModel, auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))) -> DispatchActionResponseModel:
    """Cancel a dispatch for the specified room."""
    livekit_service = get_livekit_service()
    try:
        result = await livekit_service.cancel_dispatch(body.room_name)
        if result.get("dispatch") is not None:
            result["dispatch"] = MessageToDict(result["dispatch"], preserving_proto_field_name=True)
        return DispatchActionResponseModel(**result)
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rooms/participant/{room_id}", response_model=ParticipantListResponseModel)
async def list_participants(room_id: str, auth: AuthContext = Depends(require_any_permission(AGENT_CONTROL))) -> ParticipantListResponseModel:
    """List participants in a room."""
    mongodb_service = MongoDBService()
    if not mongodb_service.connected:
        await mongodb_service.connect()
    try:
        room_object_id = ObjectId(room_id)
    except Exception:
        raise HTTPException(status_code=400, detail=f"Invalid room_id format: '{room_id}'")
    room = await mongodb_service.get_room_by_id(room_object_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    try:
        livekit_service = get_livekit_service()
        participants = await livekit_service.list_participants(room.get("room_name"))
        return ParticipantListResponseModel(
            status="ok",
            participants=[ParticipantModel(**participant) for participant in participants],
        )
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))