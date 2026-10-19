"""
Configuration module for non-realtime STT service.
"""

from .app_config import get_config, get_config_manager, reload_config, AppConfig

__all__ = ["get_config", "get_config_manager", "reload_config", "AppConfig"]
