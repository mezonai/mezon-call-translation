from google.protobuf.json_format import MessageToDict
from typing import Dict, Any, Optional, List
from bson import ObjectId

from fastapi import APIRouter, HTTPException
import httpx
from pydantic import BaseModel
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
)
from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.services.livekit_client import (
    get_livekit_service,
    LiveKitServiceError,
)

router = APIRouter()


class AccountModel(BaseModel):
    appid: str
    token: str


class DispatchRequestModel(BaseModel):
    account: AccountModel
    room_name: str


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


async def verify_account(account: Dict[str, str]) -> None:
    """
    Verify account authentication.
    Raises HTTPException if authentication fails.
    """
    try:
        is_authenticated = await authenticate_account(account)
        if not is_authenticated:
            raise HTTPException(status_code=401, detail="Authentication failed")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Authentication service timeout")
    except httpx.RequestError:
        raise HTTPException(
            status_code=503, detail="Authentication service unavailable"
        )


@router.post("/create_dispatch", response_model=DispatchActionResponseModel)
async def api_create_dispatch(
    body: DispatchRequestModel,
) -> DispatchActionResponseModel:
    """Create a dispatch for the specified room."""
    await verify_account(body.account.dict())
    livekit_service = get_livekit_service()

    try:
        result = await livekit_service.ensure_dispatch(body.room_name)
        if result.get("dispatch") is not None:
            result["dispatch"] = MessageToDict(
                result["dispatch"], preserving_proto_field_name=True
            )
        return DispatchActionResponseModel(**result)
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/cancel_dispatch", response_model=DispatchActionResponseModel)
async def api_cancel_dispatch(
    body: DispatchRequestModel,
) -> DispatchActionResponseModel:
    """Cancel a dispatch for the specified room."""
    await verify_account(body.account.dict())
    livekit_service = get_livekit_service()

    try:
        result = await livekit_service.cancel_dispatch(body.room_name)
        if result.get("dispatch") is not None:
            result["dispatch"] = MessageToDict(
                result["dispatch"], preserving_proto_field_name=True
            )
        return DispatchActionResponseModel(**result)
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/rooms/participant/{room_id}", response_model=ParticipantListResponseModel)
async def list_participants(room_id: str) -> ParticipantListResponseModel:
    """List participants in a room."""
    pg_repo = PgTranscriptRepository()
    if not pg_repo.connected:
        await pg_repo.connect()
    room = await pg_repo.get_room_by_id(room_id)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    try:
        livekit_service = get_livekit_service()
        participants = await livekit_service.list_participants(room.get("room_name"))
        return ParticipantListResponseModel(
            status="ok",
            participants=[
                ParticipantModel(**participant) for participant in participants
            ],
        )
    except LiveKitServiceError as e:
        raise HTTPException(status_code=500, detail=str(e))
