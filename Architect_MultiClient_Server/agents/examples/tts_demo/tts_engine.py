"""
TTS Engine Module - Handles Silero TTS model loading and synthesis
"""
import os
import urllib.request
from typing import Optional
import numpy as np
import torch


class TTSEngine:
    """Silero TTS Engine for text-to-speech synthesis"""
    
    def __init__(
        self,
        model_dir: str = "models",
        model_name: str = "silero_v3_en.pt",
        speaker: str = "en_0",
        sample_rate: int = 48000
    ):
        """
        Initialize TTS Engine
        
        Args:
            model_dir: Directory to store model files
            model_name: Name of the model file
            speaker: Voice speaker ID
            sample_rate: Audio sample rate in Hz
        """
        self.model_dir = model_dir
        self.model_name = model_name
        self.model_path = os.path.join(model_dir, model_name)
        self.speaker = speaker
        self.sample_rate = sample_rate
        
        self.model: Optional[torch.nn.Module] = None
        self.device = torch.device('cpu')
        self._is_loaded = False
    
    @property
    def is_loaded(self) -> bool:
        """Check if model is loaded"""
        return self._is_loaded and self.model is not None
    
    async def load(self) -> bool:
        """
        Load the TTS model from local cache or download if needed
        
        Returns:
            True if successful, False otherwise
        """
        if self._is_loaded:
            print("✅ Model already loaded")
            return True
        
        try:
            print(f"🔽 Loading Silero TTS model from {self.model_path}...")
            
            # Create model directory if needed
            os.makedirs(self.model_dir, exist_ok=True)
            
            # Download model if not exists
            if not os.path.exists(self.model_path):
                await self._download_model()
            
            # Load model
            print("🧠 Loading model into memory...")
            self.model = torch.package.PackageImporter(self.model_path).load_pickle(
                "tts_models", "model"
            )
            self.model.to(self.device)
            
            self._is_loaded = True
            print(f"✅ Model loaded successfully on {self.device}")
            return True
            
        except Exception as e:
            print(f"❌ Failed to load TTS model: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    async def _download_model(self) -> None:
        """Download model from Silero repository"""
        print("⬇️ Downloading Silero TTS model (v3_en)...")
        url = "https://models.silero.ai/models/tts/en/v3_en.pt"
        
        try:
            urllib.request.urlretrieve(url, self.model_path)
            print(f"✅ Downloaded to {self.model_path}")
        except Exception as e:
            raise RuntimeError(f"Failed to download model: {e}") from e
    
    def synthesize(self, text: str) -> np.ndarray:
        """
        Synthesize text to audio
        
        Args:
            text: Text to synthesize
            
        Returns:
            Audio data as numpy array (float32, range [-1.0, 1.0])
            
        Raises:
            RuntimeError: If model is not loaded
        """
        if not self.is_loaded:
            raise RuntimeError("Model not loaded. Call load() first.")
        
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")
        
        try:
            # Generate audio tensor
            audio_tensor = self.model.apply_tts(
                text=text,
                speaker=self.speaker,
                sample_rate=self.sample_rate
            )
            
            # Convert to numpy float32 [-1.0, 1.0]
            audio_np = audio_tensor.cpu().numpy().astype(np.float32)
            
            duration = len(audio_np) / self.sample_rate
            print(f"   🎵 Generated {duration:.2f}s audio ({len(audio_np)} samples)")
            
            return audio_np
            
        except Exception as e:
            raise RuntimeError(f"Synthesis failed: {e}") from e
    
    def get_audio_duration(self, audio_data: np.ndarray) -> float:
        """
        Calculate audio duration in seconds
        
        Args:
            audio_data: Audio samples
            
        Returns:
            Duration in seconds
        """
        return len(audio_data) / self.sample_rate
    
    def cleanup(self) -> None:
        """Cleanup resources"""
        if self.model is not None:
            del self.model
            self.model = None
        self._is_loaded = False
        print("🧹 TTS Engine cleaned up")
