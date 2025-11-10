"""
TTS Engine Service - Silero TTS model management and synthesis
Integrated with agent logging system
"""
import os
import urllib.request
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
import torch

from ..logger import get_logger

logger = get_logger(__name__)


class TTSEngine:
    """
    Silero TTS engine for text-to-speech synthesis
    Handles model loading, caching, and audio generation
    """
    
    def __init__(self, sample_rate: int = 48000, model_path: Optional[str] = None):
        """
        Initialize TTS Engine
        
        Args:
            sample_rate: Audio sample rate in Hz (default: 48000)
            model_path: Path to cached model file (default: models/silero_v3_en.pt)
        """
        self.sample_rate = sample_rate
        self.model_path = model_path or "models/silero_v3_en.pt"
        self.model = None
        self.device = torch.device('cpu')
        
        logger.info(f"TTSEngine initialized (sample_rate={sample_rate}Hz, device={self.device})")
    
    async def load(self) -> bool:
        """
        Load Silero TTS model (async with thread pool)
        Downloads model if not cached
        
        Returns:
            True if successful, False otherwise
        """
        try:
            logger.info("Loading Silero TTS model...")
            
            # Download model if needed
            if not os.path.exists(self.model_path):
                await self._download_model()
            
            # Load model in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                self.model = await loop.run_in_executor(pool, self._load_model_sync)
            
            if self.model is None:
                logger.error("Failed to load TTS model")
                return False
            
            logger.info("✅ Silero TTS model loaded successfully")
            return True
            
        except Exception as e:
            logger.error(f"Failed to load TTS model: {e}", exc_info=True)
            return False
    
    def _load_model_sync(self):
        """Synchronous model loading (runs in thread pool)"""
        try:
            # Get model directory for sys.path manipulation
            model_dir = os.path.dirname(os.path.abspath(self.model_path))
            
            # Load model using torch.hub (handles torch.package format)
            import sys
            if model_dir not in sys.path:
                sys.path.insert(0, model_dir)
            
            try:
                # Try direct load first
                model = torch.package.PackageImporter(self.model_path).load_pickle("tts_models", "model")
            except:
                # Fallback to torch.hub.load with local file
                model, _ = torch.hub.load(
                    repo_or_dir='snakers4/silero-models',
                    model='silero_tts',
                    language='en',
                    speaker='v3_en',
                    device=self.device,
                    force_reload=False,
                    trust_repo=True
                )
            
            model.to(self.device)
            logger.debug(f"Model loaded on device: {self.device}")
            return model
            
        except Exception as e:
            logger.error(f"Error in _load_model_sync: {e}", exc_info=True)
            return None
    
    async def _download_model(self):
        """Download Silero model if not cached"""
        try:
            os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
            
            model_url = "https://models.silero.ai/models/tts/en/v3_en.pt"
            logger.info(f"Downloading Silero model from {model_url}...")
            
            # Download in thread pool
            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                await loop.run_in_executor(
                    pool,
                    urllib.request.urlretrieve,
                    model_url,
                    self.model_path
                )
            
            file_size = os.path.getsize(self.model_path) / (1024 * 1024)
            logger.info(f"✅ Model downloaded successfully ({file_size:.1f} MB)")
            
        except Exception as e:
            logger.error(f"Failed to download model: {e}", exc_info=True)
            raise
    
    def synthesize(
        self,
        text: str,
        speaker: str = "en_0",
        sample_rate: Optional[int] = None
    ) -> np.ndarray:
        """
        Synthesize text to audio
        
        Args:
            text: Text to synthesize
            speaker: Speaker voice ID (default: en_0)
            sample_rate: Override sample rate (default: use engine sample_rate)
        
        Returns:
            Audio data as numpy array (float32, [-1.0, 1.0])
        
        Raises:
            RuntimeError: If model not loaded
        """
        if self.model is None:
            raise RuntimeError("TTS model not loaded. Call load() first.")
        
        try:
            target_sr = sample_rate or self.sample_rate
            
            logger.debug(f"Synthesizing: '{text[:50]}...' (speaker={speaker}, sr={target_sr}Hz)")
            
            # Generate audio using Silero
            audio = self.model.apply_tts(
                text=text,
                speaker=speaker,
                sample_rate=target_sr
            )
            
            # Convert to numpy array
            audio_np = audio.cpu().numpy()
            
            duration = len(audio_np) / target_sr
            logger.debug(f"Synthesized {duration:.2f}s audio ({len(audio_np)} samples)")
            
            return audio_np
            
        except Exception as e:
            logger.error(f"Synthesis failed for text '{text[:30]}...': {e}", exc_info=True)
            raise
    
    def get_audio_duration(self, audio_data: np.ndarray) -> float:
        """
        Calculate audio duration in seconds
        
        Args:
            audio_data: Audio samples
        
        Returns:
            Duration in seconds
        """
        return len(audio_data) / self.sample_rate
    
    def cleanup(self):
        """Release model resources"""
        try:
            if self.model is not None:
                del self.model
                self.model = None
                
                # Clear CUDA cache if using GPU
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
                
                logger.info("TTS engine resources released")
                
        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)
    
    def __del__(self):
        """Destructor - ensure cleanup"""
        self.cleanup()
