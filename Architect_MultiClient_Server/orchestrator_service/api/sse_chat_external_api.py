"""
SSE Chat External API
Endpoints for bot to receive chat external events via SSE
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.channels.chat_external_channel import ChatExternalChannel
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
chat_external_channel = ChatExternalChannel(sse_manager)


class PushChatExternalRequest(BaseModel):
    """Request model for pushing chat external events"""

    room_name: str
    room_id: str
    participant_identity: str
    message: str
    time: Optional[str] = None

    class Config:
        json_schema_extra = {
            "example": {
                "room_name": "my-room",
                "room_id": "room-12345",
                "participant_identity": "user@example.com",
                "message": "Hello from room",
                "time": "2026-03-01T10:30:00Z",
            }
        }


@router.get("/sse/chat_external")
async def sse_chat_external_endpoint(appid: str, token: str):
    """
    SSE endpoint for bot to receive chat external events.

    Args:
        appid: Application ID for authentication and connection management
        token: Authentication token

    Returns:
        StreamingResponse with SSE events
    """

    # Authenticate
    account = {"appid": appid, "token": token}
    if not await authenticate_account(account):
        return HTTPException(status_code=401, detail="Account authentication failed")

    return await chat_external_channel.create_connection(appid)


@router.post("/agent_push_chat_external")
async def push_chat_external_api(req: PushChatExternalRequest):
    """
    Push chat external event to all connected bots via SSE.

    Args:
        req: Chat external event data

    Returns:
        Status and statistics
    """
    result = await chat_external_channel.push_chat_event(
        room_name=req.room_name,
        room_id=req.room_id,
        participant_identity=req.participant_identity,
        message=req.message,
        time=req.time,
    )
    return result
