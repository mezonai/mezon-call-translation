import os
from dataclasses import dataclass
from typing import Optional

@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 10
    overlap_chunks: int = 2
    min_speech_frames: int = 10
    batch_size: int = 5
    enable_playback: bool = False

@dataclass
class WebSocketConfig:
    host: str = "localhost"
    port: int = 8000
    auth_token: str = ""
    api_key: str = ""
    auth_header: str = "Authorization"
    reconnect_max_attempts: int = 5
    reconnect_base_delay: float = 1.0
    connection_timeout: float = 10.0
    ping_interval: int = 30
    ping_timeout: int = 15
    max_queue_size: int = 32
    batch_size: int = 1
    max_buffer_size: int = 32768  # 32KB
    send_delay: float = 0.01  # 10ms

@dataclass
class BufferConfig:
    pre_speech_buffer_size: int = 10
    max_silent_streak: int = 50
    zcr_thresh_low: float = 0.02
    zcr_thresh_high: float = 0.2
    energy_thresh: float = 0.001
    ma_window: int = 8

class ConfigService:
    """Centralized configuration management"""
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ConfigService, cls).__new__(cls)
            cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        """Initialize configuration with environment overrides"""
        self.audio = AudioConfig(
            sample_rate=int(os.getenv('AUDIO_SAMPLE_RATE', 16000)),
            channels=int(os.getenv('AUDIO_CHANNELS', 1)),
            chunk_duration_ms=int(os.getenv('AUDIO_CHUNK_DURATION_MS', 10)),
            overlap_chunks=int(os.getenv('AUDIO_OVERLAP_CHUNKS', 2)),
            min_speech_frames=int(os.getenv('AUDIO_MIN_SPEECH_FRAMES', 10)),
            batch_size=int(os.getenv('AUDIO_BATCH_SIZE', 5)),
            enable_playback=bool(int(os.getenv('AUDIO_ENABLE_PLAYBACK', '0')))
        )
        
        self.websocket = WebSocketConfig(
            host=os.getenv('WS_HOST', 'localhost'),
            port=int(os.getenv('WS_PORT', 8000)),
            auth_token=os.getenv('WS_AUTH_TOKEN', ''),
            api_key=os.getenv('WS_API_KEY', ''),
            auth_header=os.getenv('WS_AUTH_HEADER', 'Authorization'),
            reconnect_max_attempts=int(os.getenv('WS_RECONNECT_MAX_ATTEMPTS', 5)),
            reconnect_base_delay=float(os.getenv('WS_RECONNECT_BASE_DELAY', 1.0)),
            connection_timeout=float(os.getenv('WS_CONNECTION_TIMEOUT', 10.0)),
            ping_interval=int(os.getenv('WS_PING_INTERVAL', 30)),
            ping_timeout=int(os.getenv('WS_PING_TIMEOUT', 15)),
            max_queue_size=int(os.getenv('WS_MAX_QUEUE_SIZE', 32)),
            batch_size=int(os.getenv('WS_BATCH_SIZE', 10)),
            max_buffer_size=int(os.getenv('WS_MAX_BUFFER_SIZE', 32768)),
            send_delay=float(os.getenv('WS_SEND_DELAY', 0.05))
        )
        
        self.buffer = BufferConfig(
            pre_speech_buffer_size=int(os.getenv('BUFFER_PRE_SPEECH_SIZE', 10)),
            max_silent_streak=int(os.getenv('BUFFER_MAX_SILENT_STREAK', 50)),
            zcr_thresh_low=float(os.getenv('BUFFER_ZCR_THRESH_LOW', 0.02)),
            zcr_thresh_high=float(os.getenv('BUFFER_ZCR_THRESH_HIGH', 0.2)),
            energy_thresh=float(os.getenv('BUFFER_ENERGY_THRESH', 0.001)),
            ma_window=int(os.getenv('BUFFER_MA_WINDOW', 8))
        )
    
    @classmethod
    def get_instance(cls) -> 'ConfigService':
        """Get singleton instance"""
        return cls()
    
    def reload(self):
        """Reload configuration from environment"""
        self._initialize()
