"""
TTS Service configuration.
"""
import os
from dataclasses import dataclass, field

@dataclass
class TTSConfig:
    lfu_cache_maxsize: int = field(default_factory=lambda: int(os.getenv("TTS_LFU_CACHE_MAXSIZE", 50)))
    model_path: str = field(default_factory=lambda: os.getenv("TTS_MODEL_PATH", "models/kokoro_models"))