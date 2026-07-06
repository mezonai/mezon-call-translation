"""
SSE Chat External API
Endpoints for bot to receive chat external events via SSE
"""

from typing import Any, ClassVar

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from orchestrator_service.api.sse.channels.chat_external_channel import ChatExternalChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.constants.permissions import CHAT_EXTERNAL_VIEW_ALL
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
chat_external_channel = ChatExternalChannel(sse_manager)


class PushChatExternalRequest(BaseModel):                           # type: ignore[explicit-any]
    """Request model for pushing chat external events"""

    room_name: str
    room_id: str
    participant_identity: str
    message: str
    time: str | None = None

    class Config:
        json_schema_extra: ClassVar[dict[str, Any]] = {
            "example": {
                "room_name": "my-room",
                "room_id": "room-12345",
                "participant_identity": "user@example.com",
                "message": "Hello from room",
                "time": "2026-03-01T10:30:00Z",
            }
        }


@router.get("/sse/chat_external")
async def sse_chat_external_endpoint(auth: AuthContext = Depends(require_any_permission(CHAT_EXTERNAL_VIEW_ALL))):
    """
    SSE endpoint for bot to receive chat external events.

    Args:

    Returns:
        StreamingResponse with SSE events
    """
    return await chat_external_channel.create_connection(auth.user_id)


@router.post("/agent_push_chat_external")
async def push_chat_external_api(req: PushChatExternalRequest, auth: dict[str, str] = Depends(verify_api_key)):
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
