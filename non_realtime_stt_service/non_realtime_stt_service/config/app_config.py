"""
Centralized application configuration for Non-Realtime STT service.

Only contains configuration relevant to batch Whisper transcription:
- Audio format validation (sample_rate, channels)
- Whisper model settings
- MinIO storage access
- Redis streams (consumer + producer)
- Transcription batching
- Metrics and logging
"""
import os
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, field
from pathlib import Path
from dotenv import load_dotenv

# Service configuration must not depend on the directory from which Uvicorn is
# launched.  Load .env relative to this file's parent package.
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

logger = logging.getLogger(__name__)


@dataclass
class AudioConfig:
    """Audio format validation configuration.
    
    Used to verify that the capture format matches what Whisper expects
    (16 kHz mono PCM16). Not used for audio processing itself.
    """
    sample_rate: int = 16000
    channels: int = 1


@dataclass
class WhisperConfig:
    """Non-realtime marker-based Whisper configuration.

    ``model_size`` is passed directly to faster-whisper as a model name or a
    local directory (e.g. ``large-v3-turbo``, ``/models/whisper``).
    """
    model_size: str = "large-v3-turbo"
    compute_type: str = "int8"  # float16, int8, int8_float16
    cpu_threads: int = 8
    temperature: float | list[float] = 0.0
    language: str = "vi"  # "auto" enables language detection


@dataclass
class TranscriptConfig:
    """Transcription batching configuration."""
    chunk_size: int = 50  # Number of segments to batch together before sending to Redis.


@dataclass
class MinIOConfig:
    """MinIO/S3 storage configuration."""
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin123"
    bucket: str = "livekit-recordings"
    secure: bool = False


@dataclass
class RedisConfig:
    """Redis configuration for streams and caching."""
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    # Timeouts and retries
    claim_min_idle_time_ms: int = 60000  # 60 seconds before claiming orphaned tasks
    block_timeout_ms: int = 5000  # Block for 5s waiting for new messages
    max_retries: int = 3  # Max retries for failed tasks
    # Connection pool
    max_connections: int = 10
    socket_timeout: float = 30.0  # Must be > block_timeout_ms/1000 + buffer
    socket_connect_timeout: float = 10.0
    # Worker heartbeat
    heartbeat_interval_sec: float = 10.0
    worker_timeout_sec: float = 30.0


@dataclass
class ServerConfig:
    """Minimal server configuration for health/metrics endpoints."""
    host: str = "0.0.0.0"
    port: int = 8001  # Different from realtime's 8000
    log_level: str = "INFO"


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
class MetricsConfig:
    """Prometheus metrics configuration."""
    enabled: bool = False
    system_metrics: bool = True  # Track CPU/memory metrics
    stt_metrics: bool = True  # Track STT/transcription metrics
    update_interval: float = 5.0  # System metrics update interval in seconds


@dataclass
class AppConfig:
    """Main application configuration for non-realtime STT."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    transcript: TranscriptConfig = field(default_factory=TranscriptConfig)
    minio: MinIOConfig = field(default_factory=MinIOConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

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
        """Load configuration from environment variables."""
        if self._config is not None:
            return self._config
        
        config = AppConfig()
        
        # Audio configuration
        config.audio.sample_rate = int(os.getenv("SAMPLE_RATE", config.audio.sample_rate))
        config.audio.channels = int(os.getenv("CHANNELS", config.audio.channels))
        
        # Whisper configuration
        config.whisper.model_size = os.getenv("WHISPER_MODEL_SIZE", config.whisper.model_size)
        config.whisper.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", config.whisper.compute_type)
        config.whisper.cpu_threads = int(os.getenv("WHISPER_CPU_THREADS", config.whisper.cpu_threads))

        temp_env = os.getenv("WHISPER_TEMPERATURE", "")
        if temp_env:
            if "," in temp_env:
                config.whisper.temperature = [float(x.strip()) for x in temp_env.split(",") if x.strip()]
            else:
                config.whisper.temperature = float(temp_env)

        config.whisper.language = os.getenv("WHISPER_LANGUAGE", config.whisper.language)
        
        # Transcription batching
        config.transcript.chunk_size = int(os.getenv("TRANSCRIPT_CHUNK_SIZE", config.transcript.chunk_size))

        # MinIO configuration
        config.minio.endpoint = os.getenv("MINIO_ENDPOINT", config.minio.endpoint)
        config.minio.access_key = os.getenv("MINIO_ACCESS_KEY", config.minio.access_key)
        config.minio.secret_key = os.getenv("MINIO_SECRET_KEY", config.minio.secret_key)
        config.minio.bucket = os.getenv("MINIO_BUCKET", config.minio.bucket)
        config.minio.secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        
        # Redis configuration
        config.redis.host = os.getenv("REDIS_HOST", config.redis.host)
        config.redis.port = int(os.getenv("REDIS_PORT", config.redis.port))
        config.redis.password = os.getenv("REDIS_PASSWORD", config.redis.password)
        config.redis.db = int(os.getenv("REDIS_DB", config.redis.db))
        config.redis.claim_min_idle_time_ms = int(os.getenv("REDIS_CLAIM_MIN_IDLE_TIME_MS", config.redis.claim_min_idle_time_ms))
        config.redis.block_timeout_ms = int(os.getenv("REDIS_BLOCK_TIMEOUT_MS", config.redis.block_timeout_ms))
        config.redis.max_retries = int(os.getenv("REDIS_MAX_RETRIES", config.redis.max_retries))
        config.redis.max_connections = int(os.getenv("REDIS_MAX_CONNECTIONS", config.redis.max_connections))
        config.redis.socket_timeout = float(os.getenv("REDIS_SOCKET_TIMEOUT", config.redis.socket_timeout))
        config.redis.socket_connect_timeout = float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", config.redis.socket_connect_timeout))
        config.redis.heartbeat_interval_sec = float(os.getenv("REDIS_HEARTBEAT_INTERVAL_SEC", config.redis.heartbeat_interval_sec))
        config.redis.worker_timeout_sec = float(os.getenv("REDIS_WORKER_TIMEOUT_SEC", config.redis.worker_timeout_sec))
        
        # Server configuration
        config.server.host = os.getenv("SERVER_HOST", config.server.host)
        config.server.port = int(os.getenv("SERVER_PORT", config.server.port))
        config.server.log_level = os.getenv("SERVER_LOG_LEVEL", config.server.log_level)
        
        # Logging configuration
        config.logging.level = os.getenv("LOG_LEVEL", config.logging.level)
        config.logging.app_log_file = os.getenv("LOG_APP_FILE", config.logging.app_log_file)
        config.logging.metrics_log_file = os.getenv("LOG_METRICS_FILE", config.logging.metrics_log_file)
        
        # Metrics configuration
        config.metrics.enabled = os.getenv("METRICS_ENABLED", "false").lower() == "true"
        config.metrics.system_metrics = os.getenv("METRICS_SYSTEM", "true").lower() == "true"
        config.metrics.stt_metrics = os.getenv("METRICS_STT", "true").lower() == "true"
        config.metrics.update_interval = float(os.getenv("METRICS_UPDATE_INTERVAL", config.metrics.update_interval))
        
        self._config = config
        logger.info("Non-realtime STT configuration loaded successfully")
        return config
    
    def get_config(self) -> AppConfig:
        """Get current configuration."""
        if self._config is None:
            return self.load_config()
        return self._config
    
    def reload_config(self) -> AppConfig:
        """Reload configuration from sources."""
        self._config = None
        return self.load_config()


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
