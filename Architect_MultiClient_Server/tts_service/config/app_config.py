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
    rotation_max_mb: int = field(default_factory=lambda: int(os.getenv('LOG_ROTATION_MAX_MB', '500')))
    backup_count: int = field(default_factory=lambda: int(os.getenv('LOG_BACKUP_COUNT', '5')))

class Config:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        self.tts = TTSConfig()
        self.logger = LoggerConfig()
        self._initialized = True

def get_config() -> Config:
    return Config()