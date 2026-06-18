from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.channels.message_channel import MessageChannel
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
    participant_identity: Optional[str] = None


@router.post("/push_transcript")
async def push_transcript_api(req: PushMessageRequest):
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
async def sse_endpoint(appid: str, token: str, room: str):
    """
    SSE endpoint for real-time message streaming.

    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token
        room: Room name to subscribe to

    Returns:
        StreamingResponse with SSE events
    """
    account = {"appid": appid, "token": token}
    if not await authenticate_account(account):
        return HTTPException(status_code=401, detail="Account authentication failed")

    return await message_channel.create_connection(appid, room)
