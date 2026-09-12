"""
SSE Chat External API
Endpoints for bot to receive chat external events via SSE
"""



from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.channels.chat_external_channel import ChatExternalChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.auth.authorization import AuthContext, require_any_permission
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.constants.permissions import CHAT_EXTERNAL_VIEW_ALL
from orchestrator_service.models.sse_chat_external_models import (
    PushChatExternalRequest,
    PushChatExternalResponse,
)

router = APIRouter()

# Initialize SSE infrastructure
sse_manager = SSEManager()
chat_external_channel = ChatExternalChannel(sse_manager)



@router.get("/sse/chat_external", response_class=StreamingResponse)
async def sse_chat_external_endpoint(
    auth: AuthContext = Depends(require_any_permission(CHAT_EXTERNAL_VIEW_ALL))
) -> StreamingResponse:
    """
    SSE endpoint for bot to receive chat external events.

    Args:
        auth: Authentication context with required permissions

    Returns:
        StreamingResponse with SSE events
    """
    return await chat_external_channel.create_connection(auth.user_id)


@router.post("/agent_push_chat_external", response_model=PushChatExternalResponse)
async def push_chat_external_api(
    req: PushChatExternalRequest,
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> PushChatExternalResponse:
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
    return PushChatExternalResponse.model_validate(result)
