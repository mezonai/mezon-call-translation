"""
Centralized application configuration management.
"""
import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()

logger = logging.getLogger(__name__)


@dataclass
class AudioConfig:
    """Audio processing configuration."""
    sample_rate: int = 16000
    min_text_length: int = 2
    translation_interval: float = 0.8
    chunk_size: int = 320
    channels: int = 1
    dtype: str = 'int16'


@dataclass
class VADConfig:
    """Voice Activity Detection configuration."""
    enabled: bool = False
    threshold: float = 0.5
    min_speech_duration_ms: int = 250
    min_silence_duration_ms: int = 100
    window_size_samples: int = 512
    cleanup_interval: float = 30.0
    max_client_idle_time: float = 300.0
    device: Optional[str] = None


@dataclass
class STTConfig:
    """Speech-to-Text configuration."""
    vosk_model_path: str = "vosk-model-small-en-us-0.15"
    min_chunks: int = 2  # Process after just 1 chunk
    max_chunks: int = 4  # Reduced from 8 to be more responsive
    min_time_threshold: float = 0.1  # 50ms - very responsive
    max_time_threshold: float = 0.2  # 200ms - reduced from 400ms
    metrics_interval_sec: float = 10.0
    client_cleanup_interval: float = 30.0
    max_client_idle_time: float = 300.0
    max_accumulated_chunks_age: float = 60.0


@dataclass
class QueueConfig:
    """Queue configuration."""
    audio_queue_maxsize: int = 100
    result_queue_maxsize: int = 100
    audio_task_queue_maxsize: int = 100


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration."""
    vad_failure_threshold: int = 3
    vad_timeout: float = 30.0
    vad_success_threshold: int = 2
    stt_failure_threshold: int = 5
    stt_timeout: float = 60.0  # Increased from 10.0 to 60.0 seconds for stability
    stt_success_threshold: int = 3


@dataclass
class ServerConfig:
    """Server configuration."""
    host: str = "0.0.0.0"
    port: int = 8000
    reload: bool = False
    log_level: str = "INFO"
    max_connections: int = 1000
    max_concurrent_clients: int = 50  # Maximum concurrent clients for per-client pipeline


@dataclass
class LoggingConfig:
    """Logging configuration."""
    level: str = "INFO"
    format: str = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    date_format: str = "%Y-%m-%d %H:%M:%S"
    app_log_file: str = "logs/app.log"
    metrics_log_file: str = "logs/metrics.log"
    max_file_size: int = 10 * 1024 * 1024  # 10MB
    backup_count: int = 5

@dataclass
class LiveKitConfig:
    """LiveKit API configuration."""
    url: str = "http://localhost:7880"
    api_key: str = "devkey"
    api_secret: str = "secret"
    agent_name: str = "Vosk-Transcription-Agent"


@dataclass
class AuthConfig:
    """Authentication configuration."""
    jwt_secret: str = "supersecret"   # để verify JWT
    jwt_algorithm: str = "HS256"

@dataclass
class AppConfig:
    """Main application configuration."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    vad: VADConfig = field(default_factory=VADConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    livekit: LiveKitConfig = field(default_factory=LiveKitConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)

    def __post_init__(self):
        """Post-initialization processing."""
        Path(self.logging.app_log_file).parent.mkdir(parents=True, exist_ok=True)
        Path(self.logging.metrics_log_file).parent.mkdir(parents=True, exist_ok=True)


class ConfigManager:
    """Configuration manager with environment variable support."""
    
    def __init__(self, config_file: Optional[str] = None):
        self.config_file = config_file
        self._config: Optional[AppConfig] = None
    
    def load_config(self) -> AppConfig:
        """Load configuration from environment variables and config file."""
        if self._config is not None:
            return self._config
        
        # Load from environment variables
        config = AppConfig()
        
        # Audio configuration
        config.audio.sample_rate = int(os.getenv("SAMPLE_RATE", config.audio.sample_rate))
        config.audio.min_text_length = int(os.getenv("MIN_TEXT_LENGTH", config.audio.min_text_length))
        config.audio.translation_interval = float(os.getenv("TRANSLATION_INTERVAL", config.audio.translation_interval))
        config.audio.chunk_size = int(os.getenv("CHUNK_SIZE", config.audio.chunk_size))
        config.audio.channels = int(os.getenv("CHANNELS", config.audio.channels))
        config.audio.dtype = os.getenv("DTYPE", config.audio.dtype)
        
        # VAD configuration
        config.vad.enabled = os.getenv("VAD_ENABLED", str(config.vad.enabled)).lower() in ("1", "true", "yes", "on")
        config.vad.threshold = float(os.getenv("VAD_THRESHOLD", config.vad.threshold))
        config.vad.min_speech_duration_ms = int(os.getenv("VAD_MIN_SPEECH_DURATION_MS", config.vad.min_speech_duration_ms))
        config.vad.min_silence_duration_ms = int(os.getenv("VAD_MIN_SILENCE_DURATION_MS", config.vad.min_silence_duration_ms))
        config.vad.window_size_samples = int(os.getenv("VAD_WINDOW_SIZE_SAMPLES", config.vad.window_size_samples))
        config.vad.cleanup_interval = float(os.getenv("VAD_CLEANUP_INTERVAL", config.vad.cleanup_interval))
        config.vad.max_client_idle_time = float(os.getenv("VAD_MAX_CLIENT_IDLE_TIME", config.vad.max_client_idle_time))
        config.vad.device = os.getenv("VAD_DEVICE", config.vad.device)
        
        # STT configuration
        config.stt.vosk_model_path = os.getenv("VOSK_MODEL_PATH", config.stt.vosk_model_path)
        config.stt.min_chunks = int(os.getenv("VOSK_MIN_CHUNKS", config.stt.min_chunks))
        config.stt.max_chunks = int(os.getenv("VOSK_MAX_CHUNKS", config.stt.max_chunks))
        config.stt.min_time_threshold = float(os.getenv("VOSK_MIN_TIME_THRESHOLD", config.stt.min_time_threshold))
        config.stt.max_time_threshold = float(os.getenv("VOSK_MAX_TIME_THRESHOLD", config.stt.max_time_threshold))
        config.stt.metrics_interval_sec = float(os.getenv("METRICS_INTERVAL_SEC", config.stt.metrics_interval_sec))
        config.stt.client_cleanup_interval = float(os.getenv("CLIENT_CLEANUP_INTERVAL", config.stt.client_cleanup_interval))
        config.stt.max_client_idle_time = float(os.getenv("MAX_CLIENT_IDLE_TIME", config.stt.max_client_idle_time))
        config.stt.max_accumulated_chunks_age = float(os.getenv("MAX_ACCUMULATED_CHUNKS_AGE", config.stt.max_accumulated_chunks_age))
        
        # LiveKit configuration
        config.livekit.url = os.getenv("LIVEKIT_URL", config.livekit.url)
        config.livekit.api_key = os.getenv("LIVEKIT_API_KEY", config.livekit.api_key)
        config.livekit.api_secret = os.getenv("LIVEKIT_API_SECRET", config.livekit.api_secret)
        config.livekit.agent_name = os.getenv("LIVEKIT_AGENT_NAME", config.livekit.agent_name)

        # Auth configuration
        config.auth.jwt_secret = os.getenv("JWT_SECRET", config.auth.jwt_secret)
        config.auth.jwt_algorithm = os.getenv("JWT_ALGORITHM", config.auth.jwt_algorithm)


        # Queue configuration
        config.queue.audio_queue_maxsize = int(os.getenv("AUDIO_QUEUE_MAXSIZE", config.queue.audio_queue_maxsize))
        config.queue.result_queue_maxsize = int(os.getenv("RESULT_QUEUE_MAXSIZE", config.queue.result_queue_maxsize))
        config.queue.audio_task_queue_maxsize = int(os.getenv("AUDIO_TASK_QUEUE_MAXSIZE", config.queue.audio_task_queue_maxsize))
        
        # Circuit breaker configuration
        config.circuit_breaker.vad_failure_threshold = int(os.getenv("VAD_CIRCUIT_BREAKER_FAILURE_THRESHOLD", config.circuit_breaker.vad_failure_threshold))
        config.circuit_breaker.vad_timeout = float(os.getenv("VAD_CIRCUIT_BREAKER_TIMEOUT", config.circuit_breaker.vad_timeout))
        config.circuit_breaker.vad_success_threshold = int(os.getenv("VAD_CIRCUIT_BREAKER_SUCCESS_THRESHOLD", config.circuit_breaker.vad_success_threshold))
        config.circuit_breaker.stt_failure_threshold = int(os.getenv("STT_CIRCUIT_BREAKER_FAILURE_THRESHOLD", config.circuit_breaker.stt_failure_threshold))
        config.circuit_breaker.stt_timeout = float(os.getenv("STT_CIRCUIT_BREAKER_TIMEOUT", config.circuit_breaker.stt_timeout))
        config.circuit_breaker.stt_success_threshold = int(os.getenv("STT_CIRCUIT_BREAKER_SUCCESS_THRESHOLD", config.circuit_breaker.stt_success_threshold))
        
        # Server configuration
        config.server.host = os.getenv("SERVER_HOST", config.server.host)
        config.server.port = int(os.getenv("SERVER_PORT", config.server.port))
        config.server.reload = os.getenv("SERVER_RELOAD", "false").lower() == "true"
        config.server.log_level = os.getenv("SERVER_LOG_LEVEL", config.server.log_level)
        config.server.max_connections = int(os.getenv("SERVER_MAX_CONNECTIONS", config.server.max_connections))
        config.server.max_concurrent_clients = int(os.getenv("MAX_CONCURRENT_CLIENTS", config.server.max_concurrent_clients))
        
        # Logging configuration
        config.logging.level = os.getenv("LOG_LEVEL", config.logging.level)
        config.logging.format = os.getenv("LOG_FORMAT", config.logging.format)
        config.logging.date_format = os.getenv("LOG_DATE_FORMAT", config.logging.date_format)
        config.logging.app_log_file = os.getenv("LOG_APP_FILE", config.logging.app_log_file)
        config.logging.metrics_log_file = os.getenv("LOG_METRICS_FILE", config.logging.metrics_log_file)
        config.logging.max_file_size = int(os.getenv("LOG_MAX_FILE_SIZE", config.logging.max_file_size))
        config.logging.backup_count = int(os.getenv("LOG_BACKUP_COUNT", config.logging.backup_count))
        
        # Load from config file if specified
        if self.config_file and Path(self.config_file).exists():
            self._load_from_file(config)
        
        self._config = config
        logger.info("Configuration loaded successfully")
        return config
    
    def _load_from_file(self, config: AppConfig):
        """Load configuration from file (JSON/YAML support can be added)."""
        # TODO: Implement file-based configuration loading
        logger.info(f"Config file loading not implemented yet: {self.config_file}")
    
    def get_config(self) -> AppConfig:
        """Get current configuration."""
        if self._config is None:
            return self.load_config()
        return self._config
    
    def reload_config(self) -> AppConfig:
        """Reload configuration from sources."""
        self._config = None
        return self.load_config()
    
    def get_config_dict(self) -> Dict[str, Any]:
        """Get configuration as dictionary."""
        config = self.get_config()
        return {
            "audio": {
                "sample_rate": config.audio.sample_rate,
                "min_text_length": config.audio.min_text_length,
                "translation_interval": config.audio.translation_interval,
                "chunk_size": config.audio.chunk_size,
                "channels": config.audio.channels,
                "dtype": config.audio.dtype
            },
            "vad": {
                "enabled": config.vad.enabled,
                "threshold": config.vad.threshold,
                "min_speech_duration_ms": config.vad.min_speech_duration_ms,
                "min_silence_duration_ms": config.vad.min_silence_duration_ms,
                "window_size_samples": config.vad.window_size_samples,
                "cleanup_interval": config.vad.cleanup_interval,
                "max_client_idle_time": config.vad.max_client_idle_time,
                "device": config.vad.device
            },
            "stt": {
                "vosk_model_path": config.stt.vosk_model_path,
                "min_chunks": config.stt.min_chunks,
                "max_chunks": config.stt.max_chunks,
                "min_time_threshold": config.stt.min_time_threshold,
                "max_time_threshold": config.stt.max_time_threshold,
                "metrics_interval_sec": config.stt.metrics_interval_sec,
                "client_cleanup_interval": config.stt.client_cleanup_interval,
                "max_client_idle_time": config.stt.max_client_idle_time,
                "max_accumulated_chunks_age": config.stt.max_accumulated_chunks_age
            },
            "queue": {
                "audio_queue_maxsize": config.queue.audio_queue_maxsize,
                "result_queue_maxsize": config.queue.result_queue_maxsize,
                "audio_task_queue_maxsize": config.queue.audio_task_queue_maxsize
            },
            "circuit_breaker": {
                "vad_failure_threshold": config.circuit_breaker.vad_failure_threshold,
                "vad_timeout": config.circuit_breaker.vad_timeout,
                "vad_success_threshold": config.circuit_breaker.vad_success_threshold,
                "stt_failure_threshold": config.circuit_breaker.stt_failure_threshold,
                "stt_timeout": config.circuit_breaker.stt_timeout,
                "stt_success_threshold": config.circuit_breaker.stt_success_threshold
            },
            "server": {
                "host": config.server.host,
                "port": config.server.port,
                "reload": config.server.reload,
                "log_level": config.server.log_level,
                "max_connections": config.server.max_connections,
                "max_concurrent_clients": config.server.max_concurrent_clients
            },
            "logging": {
                "level": config.logging.level,
                "format": config.logging.format,
                "date_format": config.logging.date_format,
                "app_log_file": config.logging.app_log_file,
                "metrics_log_file": config.logging.metrics_log_file,
                "max_file_size": config.logging.max_file_size,
                "backup_count": config.logging.backup_count
            },
            "livekit": {
                "url": config.livekit.url,
                "api_key": config.livekit.api_key,
                "api_secret": "***hidden***",   # không log secret thẳng ra
                "agent_name": config.livekit.agent_name,
            },
            "auth": {
                "jwt_secret": "***hidden***",
                "jwt_algorithm": config.auth.jwt_algorithm,
            }

        }


# Global configuration manager instance
_config_manager: Optional[ConfigManager] = None


def get_config_manager() -> ConfigManager:
    """Get or create global configuration manager."""
    global _config_manager
    if _config_manager is None:
        _config_manager = ConfigManager()
    return _config_manager


def get_config() -> AppConfig:
    """Get application configuration."""
    return get_config_manager().get_config()


def reload_config() -> AppConfig:
    """Reload application configuration."""
    return get_config_manager().reload_config()

