"""
Centralized LiveKit API Client Service
Singleton pattern for efficient connection management
"""
from typing import Optional
from contextlib import asynccontextmanager

try:
    from livekit import api
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from src.logger import get_logger
from src.config.application_config import get_config

logger = get_logger(__name__)


class LiveKitClientService:
    """
    Centralized LiveKit API client with singleton pattern.
    Provides efficient connection reuse across the application.
    """
    
    _instance: Optional["LiveKitClientService"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self._client: Optional[api.LiveKitAPI] = None
        self._initialized = True
        logger.info("LiveKitClientService initialized")
    
    @property
    def is_available(self) -> bool:
        """Check if LiveKit API is available"""
        return LIVEKIT_AVAILABLE
    
    def _validate_config(self):
        """Validate LiveKit configuration"""
        config = get_config()
        if not config.livekit.api_key or not config.livekit.api_secret:
            raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")
        if not config.livekit.http_url:
            raise ValueError("LIVEKIT_URL must be set")
        return config
    
    def get_client(self) -> api.LiveKitAPI:
        """
        Get or create LiveKit API client (lazy initialization).
        
        Returns:
            LiveKitAPI instance
            
        Raises:
            RuntimeError: If LiveKit API is not available
            ValueError: If configuration is invalid
        """
        if not LIVEKIT_AVAILABLE:
            raise RuntimeError("LiveKit API not available. Please install livekit-api package.")
        
        if self._client is None:
            config = self._validate_config()
            self._client = api.LiveKitAPI(
                url=config.livekit.http_url,
                api_key=config.livekit.api_key,
                api_secret=config.livekit.api_secret
            )
            logger.info(f"LiveKit client created for {config.livekit.http_url}")
        
        return self._client
    
    @asynccontextmanager
    async def get_client_context(self):
        """
        Context manager for LiveKit client.
        Use this when you need guaranteed cleanup after operation.
        
        Note: For most cases, use get_client() directly as it reuses connections.
        This context manager creates a NEW client that will be closed after use.
        
        Yields:
            Tuple of (LiveKitAPI, agent_name)
        """
        if not LIVEKIT_AVAILABLE:
            raise RuntimeError("LiveKit API not available. Please install livekit-api package.")
        
        config = self._validate_config()
        client = api.LiveKitAPI(
            url=config.livekit.http_url,
            api_key=config.livekit.api_key,
            api_secret=config.livekit.api_secret
        )
        
        try:
            yield client, config.livekit.agent_name
        finally:
            await client.aclose()
    
    def get_agent_name(self) -> str:
        """Get configured agent name"""
        config = get_config()
        return config.livekit.agent_name
    
    async def cleanup(self):
        """Cleanup LiveKit client connection"""
        if self._client:
            try:
                await self._client.aclose()
                logger.info("LiveKit client closed")
            except Exception as e:
                logger.error(f"Error closing LiveKit client: {e}")
            finally:
                self._client = None
    
    async def health_check(self) -> dict:
        """
        Check LiveKit service health and configuration.
        
        Returns:
            Dict with health status information
        """
        if not LIVEKIT_AVAILABLE:
            return {
                "status": "error",
                "message": "LiveKit API not available",
                "configured": False,
            }
        
        try:
            config = self._validate_config()
            return {
                "status": "ok",
                "message": "LiveKit client is ready",
                "configured": True,
                "url": config.livekit.http_url,
                "agent_name": config.livekit.agent_name,
                "has_credentials": True,
            }
        except ValueError as e:
            return {
                "status": "error",
                "message": str(e),
                "configured": False,
            }


# Global singleton instance
_livekit_service: Optional[LiveKitClientService] = None


def get_livekit_service() -> LiveKitClientService:
    """
    Get the global LiveKit client service instance.
    
    Returns:
        LiveKitClientService singleton instance
    """
    global _livekit_service
    if _livekit_service is None:
        _livekit_service = LiveKitClientService()
    return _livekit_service


async def cleanup_livekit_service():
    """Cleanup global LiveKit service"""
    global _livekit_service
    if _livekit_service:
        await _livekit_service.cleanup()
        _livekit_service = None
