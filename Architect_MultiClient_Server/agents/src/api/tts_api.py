"""
TTS API endpoints for sending TTS requests via DataChannel
"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
import json
import time
from livekit import api, rtc
import os

router = APIRouter()

# LiveKit configuration
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET")
LIVEKIT_URL = os.getenv("LIVEKIT_URL")


class TTSRequest(BaseModel):
    """TTS request model"""
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


@router.post("/tts/send", response_model=TTSResponse)
async def send_tts_request(request: TTSRequest):
    """
    Send TTS request to LiveKit room via DataChannel
    
    Args:
        request: TTSRequest containing room_name, text, language, voice
        
    Returns:
        TTSResponse with success status and details
        
    Example:
        POST /api/tts/send
        {
            "room_name": "d1olQsRvR",
            "text": "Hello world",
            "language": "en",
            "voice": "default"
        }
    """
    try:
        # Validate inputs
        if not request.room_name or not request.room_name.strip():
            raise HTTPException(status_code=400, detail="room_name is required")
        
        if not request.text or not request.text.strip():
            raise HTTPException(status_code=400, detail="text is required")
        
        # Check credentials
        if not LIVEKIT_API_KEY or not LIVEKIT_API_SECRET or not LIVEKIT_URL:
            raise HTTPException(
                status_code=500,
                detail="LiveKit credentials not configured. Check LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_URL"
            )
        
        # Create LiveKit API client
        lk_api = api.LiveKitAPI(
            LIVEKIT_URL,
            LIVEKIT_API_KEY,
            LIVEKIT_API_SECRET,
        )
        
        # Prepare TTS payload
        payload = {
            "type": "tts_request",
            "text": request.text.strip(),
            "language": request.language,
            "voice": request.voice,
            "timestamp": time.time(),
        }
        
        # Send data to room via DataChannel
        await lk_api.room.send_data(
            api.SendDataRequest(
                room=request.room_name,
                data=json.dumps(payload).encode("utf-8"),
                kind=rtc.DataPacketKind.KIND_RELIABLE,
                topic="tts_control",
            )
        )
        
        # Close API connection
        await lk_api.aclose()
        
        return TTSResponse(
            success=True,
            message="TTS request sent successfully",
            room_name=request.room_name,
            text_length=len(request.text),
            timestamp=time.time()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send TTS request: {str(e)}"
        )


@router.get("/tts/health")
async def tts_health_check():
    """
    Check TTS API health and configuration
    
    Returns:
        Health status and configuration info
    """
    has_credentials = bool(
        LIVEKIT_API_KEY and 
        LIVEKIT_API_SECRET and 
        LIVEKIT_URL
    )
    
    return {
        "status": "ok" if has_credentials else "error",
        "message": "TTS API is ready" if has_credentials else "Missing LiveKit credentials",
        "configured": has_credentials,
        "livekit_url": LIVEKIT_URL if LIVEKIT_URL else "Not configured",
        "has_api_key": bool(LIVEKIT_API_KEY),
        "has_api_secret": bool(LIVEKIT_API_SECRET),
    }
