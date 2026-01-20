"""
Centralized configuration for the agent application.
All configuration values are loaded from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field
from typing import Tuple

# Try to load .env file if dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available, use environment variables directly
    pass


# ============================================================================
# Audio Configuration
# ============================================================================

@dataclass
class AudioConfig:
    """Audio processing configuration"""
    # Basic audio parameters
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 10
    
    # Processing parameters
    overlap_chunks: int = 2
    min_speech_frames: int = 10
    max_buffer_size: int = 32768  # 32KB
    silent_threshold: int = 50
    
    # Playback (for debugging)
    enable_playback: bool = False
    
    @classmethod
    def from_env(cls) -> 'AudioConfig':
        """Create audio config from environment variables"""
        return cls(
            sample_rate=int(os.getenv('AUDIO_SAMPLE_RATE', '16000')),
            channels=int(os.getenv('AUDIO_CHANNELS', '1')),
            chunk_duration_ms=int(os.getenv('AUDIO_CHUNK_DURATION_MS', '10')),
            overlap_chunks=int(os.getenv('AUDIO_OVERLAP_CHUNKS', '2')),
            min_speech_frames=int(os.getenv('AUDIO_MIN_SPEECH_FRAMES', '10')),
            max_buffer_size=int(os.getenv('AUDIO_MAX_BUFFER_SIZE', '32768')),
            silent_threshold=int(os.getenv('AUDIO_SILENT_THRESHOLD', '50')),
            enable_playback=bool(int(os.getenv('AUDIO_ENABLE_PLAYBACK', '0')))
        )
    
    def validate(self) -> bool:
        """Validate audio configuration"""
        if self.sample_rate not in [8000, 16000, 24000, 32000, 44100, 48000]:
            return False
        if self.channels not in [1, 2]:
            return False
        if not (5 <= self.chunk_duration_ms <= 100):
            return False
        return True


# ============================================================================
# Voice Activity Detection (VAD) Configuration
# ============================================================================

@dataclass
class VADConfig:
    """Voice Activity Detection configuration"""
    # ZCR (Zero-Crossing Rate) thresholds
    zcr_thresh_low: float = 0.05
    zcr_thresh_high: float = 0.3
    
    # Energy threshold
    energy_thresh: float = 0.005
    
    # Moving average window for smoothing
    ma_window: int = 16
    
    # Speech detection parameters
    analysis_duration_ms: int = 30
    pre_speech_buffer_ms: int = 200
    min_speech_frames: int = 15
    silent_threshold_ms: int = 500
    
    @classmethod
    def from_env(cls) -> 'VADConfig':
        """Create VAD config from environment variables"""
        return cls(
            zcr_thresh_low=float(os.getenv('VAD_ZCR_THRESH_LOW', '0.05')),
            zcr_thresh_high=float(os.getenv('VAD_ZCR_THRESH_HIGH', '0.3')),
            energy_thresh=float(os.getenv('VAD_ENERGY_THRESH', '0.005')),
            ma_window=int(os.getenv('VAD_MA_WINDOW', '16')),
            analysis_duration_ms=int(os.getenv('VAD_ANALYSIS_DURATION_MS', '30')),
            pre_speech_buffer_ms=int(os.getenv('VAD_PRE_SPEECH_BUFFER_MS', '200')),
            min_speech_frames=int(os.getenv('VAD_MIN_SPEECH_FRAMES', '15')),
            silent_threshold_ms=int(os.getenv('VAD_SILENT_THRESHOLD_MS', '500'))
        )
    
    @property
    def zcr_thresh(self) -> Tuple[float, float]:
        """Get ZCR threshold as tuple"""
        return (self.zcr_thresh_low, self.zcr_thresh_high)
    
    @property
    def pre_speech_buffer_frames(self) -> int:
        """Convert pre-speech buffer from ms to frames (10ms per frame)"""
        return self.pre_speech_buffer_ms // 10
    
    @property
    def silent_threshold_frames(self) -> int:
        """Convert silent threshold from ms to frames (10ms per frame)"""
        return self.silent_threshold_ms // 10
    
    def validate(self) -> bool:
        """Validate VAD configuration"""
        if not (0 <= self.zcr_thresh_low < self.zcr_thresh_high <= 1.0):
            return False
        if not (0 <= self.energy_thresh <= 1.0):
            return False
        if self.ma_window < 3:
            return False
        return True


# ============================================================================
# WebSocket Configuration
# ============================================================================

@dataclass
class STTServiceConfig:
    """WebSocket client configuration"""
    # Connection parameters
    host: str = "localhost"
    port: int = 8000
    
    # Authentication
    auth_token: str = ""
    api_key: str = ""
    auth_header: str = "Authorization"
    
    # Connection handling
    reconnect_max_attempts: int = 5
    reconnect_base_delay: float = 1.0
    connection_timeout: float = 10.0
    
    # Keep-alive
    ping_interval: int = 30
    ping_timeout: int = 15
    
    # Data handling
    max_queue_size: int = 32
    batch_size: int = 1
    max_buffer_size: int = 32768  # 32KB
    send_delay: float = 0.01  # 10ms
    
    @classmethod
    def from_env(cls) -> 'STTServiceConfig':
        """Create WebSocket config from environment variables"""
        return cls(
            host=os.getenv('WS_HOST', 'localhost'),
            port=int(os.getenv('WS_PORT', '8000')),
            auth_token=os.getenv('WS_AUTH_TOKEN', ''),
            api_key=os.getenv('WS_API_KEY', ''),
            auth_header=os.getenv('WS_AUTH_HEADER', 'Authorization'),
            reconnect_max_attempts=int(os.getenv('WS_RECONNECT_MAX_ATTEMPTS', '5')),
            reconnect_base_delay=float(os.getenv('WS_RECONNECT_BASE_DELAY', '1.0')),
            connection_timeout=float(os.getenv('WS_CONNECTION_TIMEOUT', '10.0')),
            ping_interval=int(os.getenv('WS_PING_INTERVAL', '30')),
            ping_timeout=int(os.getenv('WS_PING_TIMEOUT', '15')),
            max_queue_size=int(os.getenv('WS_MAX_QUEUE_SIZE', '32')),
            batch_size=int(os.getenv('WS_BATCH_SIZE', '1')),
            max_buffer_size=int(os.getenv('WS_MAX_BUFFER_SIZE', '32768')),
            send_delay=float(os.getenv('WS_SEND_DELAY', '0.01'))
        )


# ============================================================================
# Buffer Configuration
# ============================================================================

@dataclass
class BufferConfig:
    """Audio buffer configuration"""
    pre_speech_buffer_size: int = 10
    max_silent_streak: int = 50
    
    @classmethod
    def from_env(cls) -> 'BufferConfig':
        """Create buffer config from environment variables"""
        return cls(
            pre_speech_buffer_size=int(os.getenv('BUFFER_PRE_SPEECH_SIZE', '10')),
            max_silent_streak=int(os.getenv('BUFFER_MAX_SILENT_STREAK', '50'))
        )


# ============================================================================
# Threading Configuration
# ============================================================================

@dataclass
class ThreadingConfig:
    """Threading configuration for audio processing"""
    processing_threads: int = 2
    max_queue_size: int = 1000
    thread_pool_timeout: float = 5.0
    
    @classmethod
    def from_env(cls) -> 'ThreadingConfig':
        """Create threading config from environment variables"""
        return cls(
            processing_threads=int(os.getenv('AUDIO_PROCESSING_THREADS', '2')),
            max_queue_size=int(os.getenv('AUDIO_MAX_QUEUE_SIZE', '1000')),
            thread_pool_timeout=float(os.getenv('AUDIO_THREAD_POOL_TIMEOUT', '5.0'))
        )
    
    def validate(self) -> bool:
        """Validate threading configuration"""
        if not (1 <= self.processing_threads <= 8):
            return False
        if not (100 <= self.max_queue_size <= 10000):
            return False
        return True


# ============================================================================
# Transcript Configuration
# ============================================================================

@dataclass
class TranscriptConfig:
    """Transcript manager configuration"""
    # Silence timeout for batching transcripts (seconds)
    silence_timeout: float = 3.0
    
    # Enable MongoDB storage
    enable_mongodb: bool = False
    
    @classmethod
    def from_env(cls) -> 'TranscriptConfig':
        """Create transcript config from environment variables"""
        return cls(
            silence_timeout=float(os.getenv('TRANSCRIPT_SILENCE_TIMEOUT', '3.0')),
            enable_mongodb=os.getenv('ENABLE_MONGODB', 'false').lower() == 'true'
        )
    
    def validate(self) -> bool:
        """Validate transcript configuration"""
        if not (0.5 <= self.silence_timeout <= 30.0):
            return False
        return True


# ============================================================================
# LiveKit Configuration
# ============================================================================

@dataclass
class LiveKitConfig:
    """LiveKit server and API configuration"""
    # Server URLs
    url: str = ""  # WebSocket URL (wss://...)
    http_url: str = ""  # HTTP URL for API calls
    
    # API credentials
    api_key: str = ""
    api_secret: str = ""
    
    # Agent configuration
    agent_name: str = "vosk-agent"
    
    # Webhook configuration (can use separate credentials)
    webhook_api_key: str = ""
    webhook_api_secret: str = ""
    verify_webhooks: bool = True
    
    # Recording
    recordings_dir: str = "/recordings"
    
    @classmethod
    def from_env(cls) -> 'LiveKitConfig':
        """Create LiveKit config from environment variables"""
        return cls(
            url=os.getenv('LIVEKIT_URL', ''),
            http_url=os.getenv('LIVEKIT_HTTP_URL', ''),
            api_key=os.getenv('LIVEKIT_API_KEY', ''),
            api_secret=os.getenv('LIVEKIT_API_SECRET', ''),
            agent_name=os.getenv('LIVEKIT_AGENT_NAME', 'vosk-agent'),
            webhook_api_key=os.getenv('LIVEKIT_WEBHOOK_API_KEY', os.getenv('LIVEKIT_API_KEY', '')),
            webhook_api_secret=os.getenv('LIVEKIT_WEBHOOK_API_SECRET', os.getenv('LIVEKIT_API_SECRET', '')),
            verify_webhooks=os.getenv('LIVEKIT_VERIFY_WEBHOOKS', 'true').lower() == 'true',
            recordings_dir=os.getenv('RECORDINGS_DIR', '/recordings'),
        )
    
    def validate(self) -> bool:
        """Validate LiveKit configuration"""
        # API key and secret are required
        if not self.api_key or not self.api_secret:
            return False
        return True


# ============================================================================
# MongoDB Configuration
# ============================================================================

@dataclass
class MongoDBConfig:
    """MongoDB configuration"""
    uri: str = "mongodb://localhost:27017"
    database: str = "mezon_transcripts"
    collection: str = "transcripts"
    enabled: bool = False
    
    @classmethod
    def from_env(cls) -> 'MongoDBConfig':
        """Create MongoDB config from environment variables"""
        return cls(
            uri=os.getenv('MONGODB_URI', 'mongodb://localhost:27017'),
            database=os.getenv('MONGODB_DATABASE', 'mezon_transcripts'),
            collection=os.getenv('MONGODB_COLLECTION', 'transcripts'),
            enabled=os.getenv('ENABLE_MONGODB', 'false').lower() == 'true',
        )


# ============================================================================
# TTS Configuration
# ============================================================================

@dataclass
class TTSConfig:
    """Text-to-Speech configuration"""
    enabled: bool = True
    model_path: str = "models/kokoro_models"
    default_language: str = "en"
    default_voice: str = "default"
    
    @classmethod
    def from_env(cls) -> 'TTSConfig':
        """Create TTS config from environment variables"""
        return cls(
            enabled=os.getenv('ENABLE_TTS', 'true').lower() == 'true',
            model_path=os.getenv('TTS_MODEL_PATH', 'models/kokoro_models'),
            default_language=os.getenv('TTS_DEFAULT_LANGUAGE', 'en'),
            default_voice=os.getenv('TTS_DEFAULT_VOICE', 'default'),
        )


# ============================================================================
# Server Configuration
# ============================================================================

@dataclass
class ServerConfig:
    """Server configuration"""
    host: str = "0.0.0.0"
    port: int = 8002
    
    # Authentication
    authenticate_account_url: str = ""
    
    @classmethod
    def from_env(cls) -> 'ServerConfig':
        """Create Server config from environment variables"""
        return cls(
            host=os.getenv('AGENT_HOST', '0.0.0.0'),
            port=int(os.getenv('AGENT_PORT', '8002')),
            authenticate_account_url=os.getenv('AUTHENTICATE_ACCOUNT_URL', ''),
        )


# ============================================================================
# MinIO/S3 Configuration
# ============================================================================

@dataclass
class MinIOConfig:
    """MinIO/S3 storage configuration for recordings"""
    endpoint: str = "http://minio:9000"
    access_key: str = "minioadmin"
    secret: str = "minioadmin123"
    bucket: str = "livekit-recordings"
    region: str = "us-east-1"
    enabled: bool = True
    
    @classmethod
    def from_env(cls) -> 'MinIOConfig':
        """Create MinIO config from environment variables"""
        return cls(
            endpoint=os.getenv('MINIO_ENDPOINT', 'http://minio:9000'),
            access_key=os.getenv('MINIO_ACCESS_KEY', 'minioadmin'),
            secret=os.getenv('MINIO_SECRET', 'minioadmin123'),
            bucket=os.getenv('MINIO_BUCKET', 'livekit-recordings'),
            region=os.getenv('MINIO_REGION', 'us-east-1'),
            enabled=os.getenv('MINIO_ENABLED', 'true').lower() == 'true',
        )
    
    def validate(self) -> bool:
        """Validate MinIO configuration"""
        if self.enabled:
            if not self.endpoint or not self.access_key or not self.secret:
                return False
            if not self.bucket:
                return False
        return True


# ============================================================================
# Logger Configuration
# ============================================================================

@dataclass
class LoggerConfig:
    """Logger configuration"""
    level: str = "INFO"
    
    @classmethod
    def from_env(cls) -> 'LoggerConfig':
        """Create Logger config from environment variables"""
        return cls(
            level=os.getenv('LOG_LEVEL', 'INFO').upper(),
        )


# ============================================================================
# Main Application Configuration (Singleton)
# ============================================================================

class Config:
    """
    Centralized configuration singleton.
    All configuration is loaded from environment variables.
    """
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Config, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Load all configuration sections
        self.audio = AudioConfig.from_env()
        self.vad = VADConfig.from_env()
        self.stt_service = STTServiceConfig.from_env()
        self.buffer = BufferConfig.from_env()
        self.threading = ThreadingConfig.from_env()
        self.transcript = TranscriptConfig.from_env()
        self.livekit = LiveKitConfig.from_env()
        self.mongodb = MongoDBConfig.from_env()
        self.tts = TTSConfig.from_env()
        self.server = ServerConfig.from_env()
        self.logger = LoggerConfig.from_env()
        self.minio = MinIOConfig.from_env()
        
        self._initialized = True
        self._validate_all()
    
    def _validate_all(self):
        """Validate all configuration sections"""
        if not self.audio.validate():
            raise ValueError("Invalid audio configuration")
        if not self.vad.validate():
            raise ValueError("Invalid VAD configuration")
        if not self.threading.validate():
            raise ValueError("Invalid threading configuration")
        if not self.transcript.validate():
            raise ValueError("Invalid transcript configuration")
    
    @classmethod
    def get_instance(cls) -> 'Config':
        """Get the singleton instance"""
        return cls()
    
    def reload(self):
        """Reload configuration from environment"""
        self._initialized = False
        self.__init__()


# ============================================================================
# Convenience functions
# ============================================================================

def get_config() -> Config:
    """Get the global configuration instance"""
    return Config.get_instance()


# ============================================================================
# Constants (backward compatibility)
# ============================================================================

# Audio constants - these are loaded from config
_config = get_config()
SAMPLE_RATE = _config.audio.sample_rate
CHANNELS = _config.audio.channels
