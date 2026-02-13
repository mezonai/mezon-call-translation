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
    channels: int = 1


@dataclass
class STTConfig:
    """Speech-to-Text configuration."""
    vosk_model_path: str = "vosk-model-small-en-us-0.15"
    min_chunks: int = 2  # Process after just 1 chunk
    max_chunks: int = 4  # Reduced from 8 to be more responsive
    min_time_threshold: float = 0.1  # 50ms - very responsive
    max_time_threshold: float = 0.2  # 200ms - reduced from 400ms
    metrics_interval_sec: float = 10.0


@dataclass
class QueueConfig:
    """Queue configuration."""
    audio_queue_maxsize: int = 100  # Per-client audio queue size


@dataclass
class CircuitBreakerConfig:
    """Circuit breaker configuration.
    
    When failure_threshold is reached, the client is disconnected immediately
    and must reconnect to get a fresh circuit breaker.
    """
    stt_failure_threshold: int = 5  # Number of consecutive failures before disconnect


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
class MinIOConfig:
    """MinIO/S3 storage configuration."""
    endpoint: str = "localhost:9000"
    access_key: str = "minioadmin"
    secret_key: str = "minioadmin123"
    bucket: str = "livekit-recordings"
    secure: bool = False  # Use HTTPS

@dataclass
class MongoDBConfig:
    host: str = "localhost"  # hoặc "mongodb" nếu chạy trong Docker
    port: int = 27017
    username: str = "root"
    password: str = "rootpassword"
    database: str = "mezon_transcripts"
    collection: str = "transcripts"
    chunk_size: int = 50  # Maximum segments per chunk

@dataclass
class WhisperConfig:
    """Whisper transcription configuration."""
    model_size: str = "medium"  # tiny, base, small, medium, large-v3
    device: str = "cpu"  # cuda or cpu
    compute_type: str = "int8"  # float16, int8, int8_float16
    beam_size: int = 1
    vad_filter: bool = True
    sample_rate: int = 16000
    language: str = ""  # Empty = auto-detect, or specify: "en", "vi", "ja", etc.


@dataclass
class MetricsConfig:
    """Prometheus metrics configuration."""
    enabled: bool = False  # Enable/disable all metrics collection
    http_metrics: bool = True  # Track HTTP request metrics
    ws_metrics: bool = True  # Track WebSocket metrics
    system_metrics: bool = True  # Track CPU/memory metrics
    stt_metrics: bool = True  # Track STT/transcription metrics
    update_interval: float = 5.0  # System metrics update interval in seconds


@dataclass
class OrchestratorConfig:
    """Orchestrator service configuration."""
    url: str = "http://localhost:8002"
    internal_api_key: str = ""


@dataclass
class AppConfig:
    """Main application configuration."""
    audio: AudioConfig = field(default_factory=AudioConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    queue: QueueConfig = field(default_factory=QueueConfig)
    circuit_breaker: CircuitBreakerConfig = field(default_factory=CircuitBreakerConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    minio: MinIOConfig = field(default_factory=MinIOConfig)
    mongodb: MongoDBConfig = field(default_factory=MongoDBConfig)
    whisper: WhisperConfig = field(default_factory=WhisperConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)

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
        config.audio.channels = int(os.getenv("CHANNELS", config.audio.channels))
        
        # STT configuration
        config.stt.vosk_model_path = os.getenv("VOSK_MODEL_PATH", config.stt.vosk_model_path)
        config.stt.min_chunks = int(os.getenv("VOSK_MIN_CHUNKS", config.stt.min_chunks))
        config.stt.max_chunks = int(os.getenv("VOSK_MAX_CHUNKS", config.stt.max_chunks))
        config.stt.min_time_threshold = float(os.getenv("VOSK_MIN_TIME_THRESHOLD", config.stt.min_time_threshold))
        config.stt.max_time_threshold = float(os.getenv("VOSK_MAX_TIME_THRESHOLD", config.stt.max_time_threshold))
        config.stt.metrics_interval_sec = float(os.getenv("METRICS_INTERVAL_SEC", config.stt.metrics_interval_sec))

        # Queue configuration
        config.queue.audio_queue_maxsize = int(os.getenv("AUDIO_QUEUE_MAXSIZE", config.queue.audio_queue_maxsize))
        
        # Circuit breaker configuration
        config.circuit_breaker.stt_failure_threshold = int(os.getenv("STT_CIRCUIT_BREAKER_FAILURE_THRESHOLD", config.circuit_breaker.stt_failure_threshold))
        
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
        
        # MinIO configuration
        config.minio.endpoint = os.getenv("MINIO_ENDPOINT", config.minio.endpoint)
        config.minio.access_key = os.getenv("MINIO_ACCESS_KEY", config.minio.access_key)
        config.minio.secret_key = os.getenv("MINIO_SECRET_KEY", config.minio.secret_key)
        config.minio.bucket = os.getenv("MINIO_BUCKET", config.minio.bucket)
        config.minio.secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
        
        # MongoDB configuration
        config.mongodb.host = os.getenv("MONGODB_HOST", config.mongodb.host)
        config.mongodb.port = int(os.getenv("MONGODB_PORT", config.mongodb.port))
        config.mongodb.username = os.getenv("MONGODB_USERNAME", config.mongodb.username)
        config.mongodb.password = os.getenv("MONGODB_PASSWORD", config.mongodb.password)
        config.mongodb.database = os.getenv("MONGODB_DATABASE", config.mongodb.database)
        config.mongodb.collection = os.getenv("MONGODB_COLLECTION", config.mongodb.collection)
        config.mongodb.chunk_size = int(os.getenv("MONGODB_CHUNK_SIZE", config.mongodb.chunk_size))
        
        # Whisper configuration
        config.whisper.enabled = os.getenv("ENABLE_WHISPER", "true").lower() == "true"
        config.whisper.model_size = os.getenv("WHISPER_MODEL_SIZE", config.whisper.model_size)
        config.whisper.device = os.getenv("WHISPER_DEVICE", config.whisper.device)
        config.whisper.compute_type = os.getenv("WHISPER_COMPUTE_TYPE", config.whisper.compute_type)
        config.whisper.beam_size = int(os.getenv("WHISPER_BEAM_SIZE", config.whisper.beam_size))
        config.whisper.vad_filter = os.getenv("WHISPER_VAD_FILTER", "true").lower() == "true"
        config.whisper.sample_rate = int(os.getenv("WHISPER_SAMPLE_RATE", config.whisper.sample_rate))
        config.whisper.language = os.getenv("WHISPER_LANGUAGE", config.whisper.language)  # "" for auto-detect
        
        # Metrics configuration
        config.metrics.enabled = os.getenv("METRICS_ENABLED", "false").lower() == "true"
        config.metrics.http_metrics = os.getenv("METRICS_HTTP", "true").lower() == "true"
        config.metrics.ws_metrics = os.getenv("METRICS_WS", "true").lower() == "true"
        config.metrics.system_metrics = os.getenv("METRICS_SYSTEM", "true").lower() == "true"
        config.metrics.stt_metrics = os.getenv("METRICS_STT", "true").lower() == "true"
        config.metrics.update_interval = float(os.getenv("METRICS_UPDATE_INTERVAL", config.metrics.update_interval))
        
        # Orchestrator configuration
        config.orchestrator.url = os.getenv("ORCHESTRATOR_URL", config.orchestrator.url)
        config.orchestrator.internal_api_key = os.getenv("ORCHESTRATOR_INTERNAL_API_KEY", config.orchestrator.internal_api_key)
        
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
                "channels": config.audio.channels
            },
            "stt": {
                "vosk_model_path": config.stt.vosk_model_path,
                "min_chunks": config.stt.min_chunks,
                "max_chunks": config.stt.max_chunks,
                "min_time_threshold": config.stt.min_time_threshold,
                "max_time_threshold": config.stt.max_time_threshold,
                "metrics_interval_sec": config.stt.metrics_interval_sec
            },
            "queue": {
                "audio_queue_maxsize": config.queue.audio_queue_maxsize
            },
            "circuit_breaker": {
                "stt_failure_threshold": config.circuit_breaker.stt_failure_threshold
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
            "orchestrator": {
                "url": config.orchestrator.url,
                "internal_api_key": "***" if config.orchestrator.internal_api_key else ""
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
