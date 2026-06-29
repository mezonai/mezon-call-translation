from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from orchestrator_service.api.sse.channels.message_channel import MessageChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.constants.permissions import ROOMS_VIEW_ALL
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
message_channel = MessageChannel(sse_manager)


class PushMessageRequest(BaseModel):
    room_name: str
    message: str
    message_type: str
    participant_identity: str | None = None


@router.post("/push_transcript")
async def push_transcript_api(req: PushMessageRequest, auth: dict[str, Any] = Depends(verify_api_key)):
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
    return result


@router.get("/sse/stream_transcript")
async def sse_endpoint(room: str, auth: AuthContext = Depends(require_any_permission(ROOMS_VIEW_ALL))):
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
