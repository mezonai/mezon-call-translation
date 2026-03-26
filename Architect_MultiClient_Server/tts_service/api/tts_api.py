from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
import numpy as np
from tts_service.services.tts_engine import KokoroVoice, get_tts_engine

router = APIRouter()

class TTSRequest(BaseModel):
    """TTS request model"""
    text: str
    voice: KokoroVoice | None = None
    speed: float | None = Field(default=None, ge=0.5, le=2.0)


@router.post("/tts/process", response_class=Response)
async def process_tts(request: TTSRequest):
    """
    Process TTS request
    """
    engine = get_tts_engine()
    selected_voice = request.voice or KokoroVoice.AF_HEART
    selected_speed = request.speed if request.speed is not None else 1.0
    audio = engine.synthesize(text=request.text, voice=selected_voice, speed=selected_speed)
    audio_int16 = (audio * 32767).astype(np.int16)
    return Response(content=audio_int16.tobytes(), media_type="application/octet-stream")
