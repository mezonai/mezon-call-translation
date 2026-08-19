"""
SSE Chat External API
Endpoints for bot to receive chat external events via SSE
"""


from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.channels.chat_external_channel import ChatExternalChannel
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.auth.verify_account import authenticate_account
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
    appid: Annotated[str, Query(min_length=1, description="Application ID")],
    token: Annotated[str, Query(min_length=1, description="Account authentication token")]
) -> StreamingResponse:
    """
    SSE endpoint for bot to receive chat external events.

    Args:
        appid: Application ID for authentication and connection management
        token: Account authentication token

    Returns:
        StreamingResponse with SSE events
    """

    # Authenticate
    account = {"appid": appid, "token": token}
    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")

    return await chat_external_channel.create_connection(appid)


@router.post("/agent_push_chat_external", response_model=PushChatExternalResponse)
async def push_chat_external_api(req: PushChatExternalRequest) -> PushChatExternalResponse:
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
