"""
Configuration package - Centralized configuration management
All configuration is loaded from a single source
"""

from .config import (
    Config,
    AudioConfig,
    VADConfig,
    WebSocketConfig,
    BufferConfig,
    ThreadingConfig,
    get_config,
    SAMPLE_RATE,
    CHANNELS
)

__all__ = [
    'Config',
    'AudioConfig',
    'VADConfig',
    'WebSocketConfig',
    'BufferConfig',
    'ThreadingConfig',
    'get_config',
    'SAMPLE_RATE',
    'CHANNELS'
]

