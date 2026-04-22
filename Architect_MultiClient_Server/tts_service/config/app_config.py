"""
TTS Service configuration.
"""
import os
from dataclasses import dataclass, field

@dataclass
class TTSConfig:
    max_cache_size: int = field(default_factory=lambda: int(os.getenv("TTS_MAX_CACHE_SIZE", 100 * 1024 * 1024)))
