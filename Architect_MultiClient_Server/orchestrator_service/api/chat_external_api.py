"""
TTS API endpoints for sending TTS requests via DataChannel
"""
from livekit import api
from livekit.api import twirp_client
from livekit.protocol.models import DataPacket

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
import time
import uuid
import json

from orchestrator_service.auth.verify_account import authenticate_account
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.utils.logger import get_logger
logger = get_logger(__name__)
router = APIRouter()


class AccountModel(BaseModel):
    appid: str
    token: str


class ChatExternalRequest(BaseModel):
    """TTS request model"""
    account: AccountModel
    room_name: str
    text: str


class ChatExternalResponse(BaseModel):
    """TTS response model"""
    success: bool
    message: str
    room_name: str
    text_length: int
    timestamp: float

    
async def send_chat_to_room(room_name: str, text: str):
    """
    Internal function to send chat request to LiveKit room via DataChannel
    
    Args:
        room_name: LiveKit room name
        text: Text to send as chat message
        
    Returns:
        Dict with status and message
    """
    service = get_livekit_service()
    
    if not service.is_available:
        return {
            "status": "error",
            "message": "LiveKit API not available. Please install livekit-api package."
        }
    
    # Validate text
    if not text or not text.strip():
        return {
            "status": "error",
            "message": "Text is required and cannot be empty"
        }
    
    try:
        lkapi = service.get_client()
        
        # Create chat message payload matching client format
        payload = {
            "id": str(uuid.uuid4()),
            "timestamp": int(time.time() * 1000),
            "message": text.strip(),
            "ignoreLegacy": False,
        }
        
        # Send data to room via DataChannel (broadcast to all participants)
        await lkapi.room.send_data(
            api.SendDataRequest(
                room=room_name,
                data=json.dumps(payload).encode("utf-8"),
                kind=DataPacket.Kind.RELIABLE,
                topic="lk-chat-topic",
                # Note: destination_identities can be added to send to specific participants
            )
        )
        
        return {
            "status": "success",
            "message": "Sent chat external successfully",
            "room_name": room_name,
            "text_length": len(text),
            "timestamp": time.time()
        }
        
    except twirp_client.TwirpError as e:
        return {
            "status": "error",
            "message": f"LiveKit server error: {e}"
        }
    except Exception as e:
        logger.error(f"Failed to send chat external message: {e}")
        return {
            "status": "error",
            "message": f"Failed to send chat external message: {str(e)}"
        }


@router.post("/chat_external/send_message", response_model=ChatExternalResponse)
async def api_send_chat_external(request: ChatExternalRequest):
    """
    API endpoint to send chat message to LiveKit room via DataChannel
    """
    # Authenticate account
    account = request.account.dict()
    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")
    
    # Validate room_name
    if not request.room_name or not request.room_name.strip():
        raise HTTPException(status_code=400, detail="room_name is required")
    
    # Send TTS request
    result = await send_chat_to_room(
        room_name=request.room_name,
        text=request.text,
    )
    
    # Handle errors
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    # Return success response
    return ChatExternalResponse(
        success=True,
        message=result["message"],
        room_name=result["room_name"],
        text_length=result["text_length"],
        timestamp=result["timestamp"]
    )
