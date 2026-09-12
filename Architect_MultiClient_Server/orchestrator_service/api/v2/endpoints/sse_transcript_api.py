from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.channels.message_channel import MessageChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL
from orchestrator_service.models.room_registry_models import RoomNameField
from orchestrator_service.models.sse_transcript_models import PushMessageRequest, PushMessageResponse
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
message_channel = MessageChannel(sse_manager)



@router.post("/push_transcript", response_model=PushMessageResponse)
async def push_transcript_api(
    req: PushMessageRequest,
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> PushMessageResponse:
    """
    Push transcript to all SSE connections in a room.

    Args:
        req: Push transcript request

    Returns:
        Status and statistics
    """
    result = await message_channel.push_message(
        room=req.room_name,
        message=req.message,
        message_type=req.message_type,
        participant_identity=req.participant_identity,
    )
    return PushMessageResponse.model_validate(result)


@router.get("/sse/stream_transcript", response_class=StreamingResponse)
async def sse_endpoint(
    room: Annotated[RoomNameField, Query(description="Room name to subscribe to")],
    auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL))
) -> StreamingResponse:
    """
    SSE endpoint for real-time message streaming.

    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token
        room: Room name to subscribe to

    Returns:
        StreamingResponse with SSE events
    """
    return await message_channel.create_connection(auth.user_id, room)
