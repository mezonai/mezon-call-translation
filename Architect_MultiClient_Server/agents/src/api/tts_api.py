"""
TTS API endpoints for sending TTS requests via DataChannel
"""
try:
    from livekit import api, rtc
    from livekit.api import twirp_client
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import time
from src.auth.verify_account import authenticate_account
from src.services.livekit_client import get_livekit_service

router = APIRouter()


class AccountModel(BaseModel):
    appid: str
    token: str


class TTSRequest(BaseModel):
    """TTS request model"""
    account: AccountModel
    room_name: str
    text: str
    language: Optional[str] = "en"
    voice: Optional[str] = "default"


class TTSResponse(BaseModel):
    """TTS response model"""
    success: bool
    message: str
    room_name: str
    text_length: int
    timestamp: float


async def send_tts_to_room(room_name: str, text: str, language: str = "en", voice: str = "default"):
    """
    Internal function to send TTS request to LiveKit room via DataChannel
    
    Args:
        room_name: LiveKit room name
        text: Text to synthesize
        language: Language code (default: "en")
        voice: Voice name (default: "default")
        
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
        
        # Prepare TTS payload
        payload = {
            "type": "tts_request",
            "text": text.strip(),
            "language": language,
            "voice": voice,
            "timestamp": time.time(),
        }
        
        # Send data to room via DataChannel
        await lkapi.room.send_data(
            api.SendDataRequest(
                room=room_name,
                data=json.dumps(payload).encode("utf-8"),
                kind=rtc.DataPacketKind.KIND_RELIABLE,
                topic="tts_control",
            )
        )
        
        return {
            "status": "success",
            "message": "TTS request sent successfully",
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
        return {
            "status": "error",
            "message": f"Failed to send TTS request: {str(e)}"
        }


@router.post("/tts/speak", response_model=TTSResponse)
async def api_send_tts_request(request: TTSRequest):
    """
    Send TTS request to LiveKit room via DataChannel
    
    Requires authentication via account credentials.
    
    Args:
        request: TTSRequest containing account, room_name, text, language, voice
        
    Returns:
        TTSResponse with success status and details
        
    Example:
        POST /api/tts/speak
        {
            "account": {
                "appid": "your_app_id",
                "token": "your_token"
            },
            "room_name": "d1olQsRvR",
            "text": "Hello world",
            "language": "en",
            "voice": "default"
        }
    """
    # Authenticate account
    account = request.account.dict()
    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")
    
    # Validate room_name
    if not request.room_name or not request.room_name.strip():
        raise HTTPException(status_code=400, detail="room_name is required")
    
    # Send TTS request
    result = await send_tts_to_room(
        room_name=request.room_name,
        text=request.text,
        language=request.language,
        voice=request.voice
    )
    
    # Handle errors
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    
    # Return success response
    return TTSResponse(
        success=True,
        message=result["message"],
        room_name=result["room_name"],
        text_length=result["text_length"],
        timestamp=result["timestamp"]
    )


@router.get("/tts/health")
async def tts_health_check():
    """
    Check TTS API health and configuration
    
    Returns:
        Health status and configuration info
    """
    service = get_livekit_service()
    return await service.health_check()

