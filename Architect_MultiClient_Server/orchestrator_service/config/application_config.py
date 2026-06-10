"""
Centralized configuration for the agent application.
All configuration values are loaded from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass, field

# Try to load .env file if dotenv is available
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not available, use environment variables directly
    pass


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
# PostgreSQL Configuration (primary database)
# ============================================================================

@dataclass
class PostgreSQLConfig:
    """PostgreSQL database configuration (primary persistence layer)"""
    host: str = "localhost"
    port: int = 5432
    username: str = "postgres"
    password: str = "postgres"
    database: str = "mezon_transcripts"
    # Connection pool sizing
    pool_size: int = 10
    max_overflow: int = 20

    @classmethod
    def from_env(cls) -> 'PostgreSQLConfig':
        """Create PostgreSQL config from environment variables"""
        return cls(
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=int(os.getenv('POSTGRES_PORT', '5432')),
            username=os.getenv('POSTGRES_USER', 'postgres'),
            password=os.getenv('POSTGRES_PASSWORD', 'postgres'),
            database=os.getenv('POSTGRES_DATABASE', 'mezon_transcripts'),
            pool_size=int(os.getenv('POSTGRES_POOL_SIZE', '10')),
            max_overflow=int(os.getenv('POSTGRES_MAX_OVERFLOW', '20')),
        )

    @property
    def async_url(self) -> str:
        """SQLAlchemy async connection URL (asyncpg driver)"""
        return (
            f"postgresql+asyncpg://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )

    @property
    def sync_url(self) -> str:
        """SQLAlchemy sync connection URL (psycopg2, for Alembic)"""
        return (
            f"postgresql+psycopg2://{self.username}:{self.password}"
            f"@{self.host}:{self.port}/{self.database}"
        )


# ============================================================================
# Stt Configuration
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
            host=os.getenv('STT_HOST', 'localhost'),
            port=int(os.getenv('STT_PORT', '8000')),
            auth_token=os.getenv('STT_AUTH_TOKEN', ''),
            api_key=os.getenv('STT_API_KEY', ''),
            auth_header=os.getenv('STT_AUTH_HEADER', 'Authorization'),
            reconnect_max_attempts=int(os.getenv('STT_RECONNECT_MAX_ATTEMPTS', '5')),
            reconnect_base_delay=float(os.getenv('STT_RECONNECT_BASE_DELAY', '1.0')),
            connection_timeout=float(os.getenv('STT_CONNECTION_TIMEOUT', '10.0')),
            ping_interval=int(os.getenv('STT_PING_INTERVAL', '30')),
            ping_timeout=int(os.getenv('STT_PING_TIMEOUT', '15')),
            max_queue_size=int(os.getenv('STT_MAX_QUEUE_SIZE', '32')),
            batch_size=int(os.getenv('STT_BATCH_SIZE', '1')),
            max_buffer_size=int(os.getenv('STT_MAX_BUFFER_SIZE', '32768')),
            send_delay=float(os.getenv('STT_SEND_DELAY', '0.01'))
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
# Auth/JWT Configuration
# ============================================================================

@dataclass
class AuthConfig:
    """Authentication and token configuration"""
    jwt_secret: str = ""
    jwt_expiry_days: int = 1
    refresh_token_expiry_days: int = 5
    internal_api_secret: str = ""

    @classmethod
    def from_env(cls) -> 'AuthConfig':
        """Create Auth config from environment variables"""
        return cls(
            jwt_secret=os.getenv('JWT_SECRET', ''),
            jwt_expiry_days=int(os.getenv('JWT_EXPIRY_DAYS', '1')),
            refresh_token_expiry_days=int(os.getenv('REFRESH_TOKEN_EXPIRY_DAYS', '5')),
            internal_api_secret=os.getenv('INTERNAL_API_SECRET', ''),
        )


# ============================================================================
# LLM Configuration
# ============================================================================

@dataclass
class LLMConfig:
    """Unified LLM configuration for all providers (Gemini, Local, OpenAI, etc.)"""
    provider: str = "local" # "gemini" | "local" | "openai"
    api_key: str = ""
    model: str  = "Qwen3.5-35B-A3B"
    base_url: str = None
    timeout: int = 120
    language: str = "Vietnamese"
    # Fallback to a Gemini-family model when primary LLM fails
    fallback_enabled: bool = False
    fallback_api_key: str = ""
    fallback_model: str = "gemma-4-31b-it"

    @classmethod
    def from_env(cls) -> 'LLMConfig':
        return cls(
            provider=os.getenv('LLM_PROVIDER', 'local').lower(),
            base_url=os.getenv('LLM_URL', 'http://localhost:8080/v1/chat/completions'),
            model=os.getenv('LLM_MODEL', 'Qwen3.5-35B-A3B'),
            api_key=os.getenv('LLM_API_KEY', ''),
            timeout=int(os.getenv('LLM_TIMEOUT', '120')),
            language=os.getenv('LLM_LANGUAGE', 'Vietnamese'),
            fallback_enabled=os.getenv('LLM_FALLBACK_ENABLED', 'false').lower() == 'true',
            fallback_api_key=os.getenv('LLM_FALLBACK_API_KEY', ''),
            fallback_model=os.getenv('LLM_FALLBACK_MODEL', 'gemma-4-31b-it'),
        )

    def validate(self) -> bool:
        # Optional validation if needed
        return True


# ============================================================================
# Redis Configuration (Task Queue)
# ============================================================================

@dataclass
class RedisConfig:
    """Redis configuration for transcription task queue."""
    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    # Connection pool
    max_connections: int = 10
    socket_timeout: float = 30.0
    socket_connect_timeout: float = 10.0
    block_timeout_ms: int = 5000  # Timeout for blocking operations (e.g., XREADGROUP)
    
    @classmethod
    def from_env(cls) -> 'RedisConfig':
        """Create Redis config from environment variables"""
        
        return cls(
            host=os.getenv('REDIS_HOST', 'localhost'),
            port=int(os.getenv('REDIS_PORT', '6379')),
            password=os.getenv('REDIS_PASSWORD', ''),
            db=int(os.getenv('REDIS_DB', '0')),
            max_connections=int(os.getenv('REDIS_MAX_CONNECTIONS', '10')),
            socket_timeout=float(os.getenv('REDIS_SOCKET_TIMEOUT', '30.0')),
            socket_connect_timeout=float(os.getenv('REDIS_SOCKET_CONNECT_TIMEOUT', '10.0')),
            block_timeout_ms=int(os.getenv('REDIS_BLOCK_TIMEOUT_MS', '5000')),
        )


# ============================================================================
# Notification Configuration (Mezon Webhook)
# ============================================================================

@dataclass
class NotificationConfig:
    """Configuration for sending notifications via Mezon webhooks"""
    enabled: bool = True
    stream_key: str = "notifications"  # Redis stream key
    group_name: str = "notification-workers"  # Consumer group
    webhook_endpoint: str = "https://webhook.mezon.ai/webhooks"
    max_retries: int = 3
    retry_delay_sec: int = 5
    channel_id: str = ""  # Target Mezon channel ID
    webhook_token: str = ""  # Webhook authentication token
    
    @classmethod
    def from_env(cls) -> 'NotificationConfig':
        """Create Notification config from environment variables"""
        return cls(
            enabled=os.getenv('NOTIFICATION_ENABLED', 'true').lower() == 'true',
            stream_key=os.getenv('NOTIFICATION_STREAM_KEY', 'notifications'),
            group_name=os.getenv('NOTIFICATION_GROUP_NAME', 'notification-workers'),
            webhook_endpoint=os.getenv('NOTIFICATION_WEBHOOK_ENDPOINT', 'https://your_web_hook/webhooks'),
            max_retries=int(os.getenv('NOTIFICATION_MAX_RETRIES', '3')),
            retry_delay_sec=int(os.getenv('NOTIFICATION_RETRY_DELAY_SEC', '5')),
            channel_id=os.getenv('NOTIFICATION_CHANNEL_ID', ''),
            webhook_token=os.getenv('NOTIFICATION_WEBHOOK_TOKEN', ''),
        )


# ============================================================================
# OAuth2 Configuration
# ============================================================================

@dataclass
class OAuth2Config:
    """Mezon OAuth2 configuration"""
    client_id: str = ""
    client_secret: str = ""
    redirect_uri: str = "http://localhost:3000/callback"
    auth_url: str = "https://your-oauth2-domain/oauth2/auth"
    token_url: str = "https://your-oauth2-domain/oauth2/token"
    userinfo_url: str = "https://your-oauth2-domain/userinfo"

    @classmethod
    def from_env(cls) -> 'OAuth2Config':
        """Create OAuth2 config from environment variables"""
        return cls(
            client_id=os.getenv('MEZON_CLIENT_ID', ''),
            client_secret=os.getenv('MEZON_CLIENT_SECRET', ''),
            redirect_uri=os.getenv('MEZON_REDIRECT_URI', 'http://localhost:3000/callback'),
            auth_url=os.getenv('MEZON_AUTH_URL', 'https://your-oauth2-domain/oauth2/auth'),
            token_url=os.getenv('MEZON_TOKEN_URL', 'https://your-oauth2-domain/oauth2/token'),
            userinfo_url=os.getenv('MEZON_USERINFO_URL', 'https://your-oauth2-domain/userinfo'),
        )

    def validate(self) -> bool:
        """Validate OAuth2 configuration"""
        # Client ID and Secret are required
        return bool(self.client_id and self.client_secret)


# ============================================================================
# Outbox Worker Configuration
# ============================================================================

@dataclass
class OutboxConfig:
    """Configuration for the Summary Outbox Worker"""
    check_interval_sec: int = 30
    delay_between_items_sec: int = 30
    batch_limit: int = 5
    retry_summarization_target_hours: list = field(default_factory=lambda: [19, 20, 21])

    @classmethod
    def from_env(cls) -> 'OutboxConfig':
        hours_str = os.getenv("OUTBOX_RETRY_SUMMARIZATION_TARGET_HOURS", "19,20,21")
        try:
            target_hours = [int(h.strip()) for h in hours_str.split(",") if h.strip()]
        except ValueError:
            target_hours = [19, 20, 21]
        return cls(
            check_interval_sec=int(os.getenv("OUTBOX_CHECK_INTERVAL_SEC", "30")),
            delay_between_items_sec=int(os.getenv("OUTBOX_DELAY_BETWEEN_ITEMS_SEC", "30")),
            batch_limit=int(os.getenv("OUTBOX_BATCH_LIMIT", "5")),
            retry_summarization_target_hours=target_hours,
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
        self.livekit = LiveKitConfig.from_env()
        self.stt_service = STTServiceConfig.from_env()
        self.postgresql = PostgreSQLConfig.from_env()
        self.server = ServerConfig.from_env()
        self.logger = LoggerConfig.from_env()
        self.auth = AuthConfig.from_env()
        self.minio = MinIOConfig.from_env()
        self.llm = LLMConfig.from_env()
        self.redis = RedisConfig.from_env()
        self.notification = NotificationConfig.from_env()
        self.oauth2 = OAuth2Config.from_env()
        self.outbox = OutboxConfig.from_env()

        self._initialized = True
        self._validate_all()

    def _validate_all(self):
        """Validate all configuration sections"""
        if not self.livekit.validate():
            raise ValueError("Invalid LiveKit configuration")
        if not self.minio.validate():
            raise ValueError("Invalid MinIO configuration")
        if not self.oauth2.validate():
            raise ValueError("Invalid OAuth2 configuration")
    
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
