"""
TTS Engine Service - Kokoro TTS model management and synthesis
Integrated with agent logging system
"""
import os
import asyncio
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
from pathlib import Path
from kokoro import KPipeline

from ..logger import get_logger

logger = get_logger(__name__)


class TTSEngine:
    """
    Kokoro TTS engine for text-to-speech synthesis
    Handles model loading, caching, and audio generation
    """
    
    def __init__(self, sample_rate: int = 24000, model_path: Optional[str] = None):
        """
        Initialize TTS Engine
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 24000 for Kokoro)
            model_path: Path to model directory (default: models/kokoro_models)
        """
        self.sample_rate = sample_rate
        self.model_dir = Path(model_path or "models/kokoro_models")
        self.pipeline = None
        self.lang_code = 'a'  # 'a' = American English, 'b' = British English
        
        logger.info(f"TTSEngine initialized (sample_rate={sample_rate}Hz, model=Kokoro-82M)")
    
    async def load(self) -> bool:
        """
        Load Kokoro TTS model (async with thread pool)
        Initializes KPipeline for audio generation
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Loading Kokoro TTS model...")
            
            # Check if model directory exists
            if not self.model_dir.exists():
                logger.warning(f"Model directory not found: {self.model_dir}")
                logger.info("Please download Kokoro model first using download script")
                return False
            
            # Load model in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                self.pipeline = await loop.run_in_executor(pool, self._load_model_sync)
            
            if self.pipeline is None:
                logger.error("Failed to load TTS model")
                return False
            
            logger.info("✅ Kokoro TTS model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load TTS model: {e}", exc_info=True)
            return False
    
    def _load_model_sync(self):
        """Synchronous model loading (runs in thread pool)"""
        try:
            # Import Kokoro inside thread to avoid import issues
            
            
            # Set HF_HOME to use local model directory
            os.environ['HF_HOME'] = str(self.model_dir.parent)
            
            # Initialize Kokoro pipeline
            pipeline = KPipeline(lang_code=self.lang_code, repo_id='hexgrad/Kokoro-82M')
            
            logger.debug(f"Kokoro pipeline initialized (lang_code={self.lang_code})")
            return pipeline
            
        except Exception as e:
            logger.error(f"Error in _load_model_sync: {e}", exc_info=True)
            logger.error("Please ensure 'kokoro' package is installed: pip install kokoro")
            return None
    

    
    def synthesize(
        self,
        text: str,
        voice: str = "af_heart",
        speed: float = 1.0
    ) -> np.ndarray:
        """
        Synthesize text to audio using Kokoro TTS
        
        Args:
            text: Text to synthesize
            voice: Voice name (default: af_heart - American Female)
                   Available: af_heart, af_bella, af_sarah, am_adam, am_michael, etc.
            speed: Speech speed multiplier (0.5-2.0, default: 1.0)
        
        Returns:
            Audio data as numpy array (float32, [-1.0, 1.0])
        
        Raises:
            RuntimeError: If model not loaded
        """
        if self.pipeline is None:
            raise RuntimeError("TTS model not loaded. Call load() first.")
        
        try:
            logger.debug(f"Synthesizing: '{text[:50]}...' (voice={voice}, speed={speed}x)")
            
            # Generate audio using Kokoro
            generator = self.pipeline(
                text,
                voice=voice,
                speed=speed,
                split_pattern=r'\n+'
            )
            
            # Collect all audio chunks
            all_audio = []
            for graphemes, phonemes, audio in generator:
                all_audio.append(audio)
            
            # Concatenate chunks
            if not all_audio:
                logger.warning("No audio generated for text")
                return np.array([], dtype=np.float32)
            
            audio_np = np.concatenate(all_audio)
            
            duration = len(audio_np) / self.sample_rate
            logger.debug(f"Synthesized {duration:.2f}s audio ({len(audio_np)} samples)")
            
            return audio_np
            
        except Exception as e:
            logger.error(f"Synthesis failed for text '{text[:30]}...': {e}", exc_info=True)
            raise
    
    def cleanup(self):
        """Release model resources"""
        try:
            if self.pipeline is not None:
                del self.pipeline
                self.pipeline = None
                
                logger.info("TTS engine resources released")
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
    
    def __del__(self):
        """Destructor - ensure cleanup"""
        self.cleanup()


_tts_engine: Optional[TTSEngine] = None

@lru_cache(maxsize=1)
def get_tts_engine() -> TTSEngine:
    """Get the TTS engine singleton instance"""
    if _tts_engine is None:
        _tts_engine = TTSEngine()
    return _tts_engine
