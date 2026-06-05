"""
TTS Service configuration.
"""
import os
from dataclasses import dataclass, field

@dataclass
class TTSConfig:
    lfu_cache_maxsize: int = field(default_factory=lambda: int(os.getenv("TTS_LFU_CACHE_MAXSIZE", 50)))
    model_path: str = field(default_factory=lambda: os.getenv("TTS_MODEL_PATH", "models/kokoro_models"))

@dataclass
class LoggerConfig:
    level: str = field(default_factory=lambda: os.getenv('LOG_LEVEL', 'INFO').upper())

class Config:
    _instance = None

    def __init__(self):
        self.tts = TTSConfig()
        self.logger = LoggerConfig()

    @classmethod
    def get_instance(cls) -> 'Config':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

def get_config() -> Config:
    """Get the global configuration instance."""
    return Config.get_instance()