"""
TTS Client - Handles communication with TTS service API
"""
import httpx
from typing import Optional
import numpy as np
from src.logger import get_logger
from src.config.application_config import get_config

logger = get_logger(__name__)
config = get_config()


async def process_text_to_audio(text: str) -> Optional[np.ndarray]:
    """
    Process text to audio using the TTS client.
    
    Args:
        text: Text to process
    
    Returns:
        Audio data as numpy array (float32, [-1.0, 1.0])
    """
    if not config.tts_service.base_url:
        logger.warning("TTS client base URL not configured, skipping text to audio processing")
        return None

    url = f"{config.tts_service.base_url}/api/tts/process"
    payload = {
        "text": text
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
            if response.status_code in [200, 201]:
                audio = np.frombuffer(response.content, dtype=np.int16) / 32767.0
                return audio
            else:
                logger.error(f"Failed to process text to audio: HTTP {response.status_code} - {response.text}")
                raise Exception(f"Failed to process text to audio: HTTP {response.status_code} - {response.text}")
    except Exception as e:
        logger.error(f"Unexpected error processing text to audio: {e}", exc_info=True)
        raise Exception(f"Unexpected error processing text to audio: {e}")
