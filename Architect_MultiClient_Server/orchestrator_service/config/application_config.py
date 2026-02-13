"""
Centralized configuration for the agent application.
All configuration values are loaded from environment variables with sensible defaults.
"""

import os
from dataclasses import dataclass
from typing import Tuple

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
# MongoDB Configuration
# ============================================================================

@dataclass
class MongoDBConfig:
    host: str = "localhost"  # hoặc "mongodb" nếu chạy trong Docker
    port: int = 27017
    username: str = "root"
    password: str = "rootpassword"
    database: str = "mezon_transcripts"
    collection: str = "transcripts"
    
    @classmethod
    def from_env(cls) -> 'MongoDBConfig':
        """Create MongoDB config from environment variables"""
        return cls(
            host=os.getenv('MONGODB_HOST', 'localhost'),
            port=int(os.getenv('MONGODB_PORT', '27017')),
            username=os.getenv('MONGODB_USERNAME', 'root'),
            password=os.getenv('MONGODB_PASSWORD', 'rootpassword'),
            database=os.getenv('MONGODB_DATABASE', 'mezon_transcripts'),
            collection=os.getenv('MONGODB_COLLECTION', 'transcripts'),
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
    internal_api_key: str = ""
    
    @classmethod
    def from_env(cls) -> 'ServerConfig':
        """Create Server config from environment variables"""
        return cls(
            host=os.getenv('AGENT_HOST', '0.0.0.0'),
            port=int(os.getenv('AGENT_PORT', '8002')),
            authenticate_account_url=os.getenv('AUTHENTICATE_ACCOUNT_URL', ''),
            internal_api_key=os.getenv('INTERNAL_API_KEY', 'my-secret-internal-key'),
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
# LLM Configuration
# ============================================================================

@dataclass
class LLMConfig:
    """Configuration for LLM services (Gemini, OpenAI, etc.)"""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    
    @classmethod
    def from_env(cls) -> 'LLMConfig':
        return cls(
            gemini_api_key=os.getenv('GEMINI_API_KEY', ''),
            gemini_model=os.getenv('GEMINI_MODEL', 'gemini-2.5-flash'),
        )
    
    def validate(self) -> bool:
        # Optional validation if needed
        return True


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
        self.mongodb = MongoDBConfig.from_env()
        self.server = ServerConfig.from_env()
        self.logger = LoggerConfig.from_env()
        self.minio = MinIOConfig.from_env()
        self.llm = LLMConfig.from_env()
        
        self._initialized = True
        self._validate_all()
    
    def _validate_all(self):
        """Validate all configuration sections"""
        if not self.livekit.validate():
            raise ValueError("Invalid LiveKit configuration")
        if not self.minio.validate():
            raise ValueError("Invalid MinIO configuration")
    
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
