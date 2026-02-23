from fastapi import APIRouter, Response
from pydantic import BaseModel
import numpy as np
from tts_service.services.tts_engine import get_tts_engine

router = APIRouter()

class TTSRequest(BaseModel):
    """TTS request model"""
    text: str

@router.post("/tts/process", response_class=Response)
async def process_tts(request: TTSRequest):
    """
    Process TTS request
    """
    engine = get_tts_engine()
    audio = engine.synthesize(request.text)
    audio_int16 = (audio * 32767).astype(np.int16)
    return Response(content=audio_int16.tobytes(), media_type="application/octet-stream")
