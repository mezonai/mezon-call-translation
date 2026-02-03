"""
Configuration package - Centralized configuration management
All configuration is loaded from a single source
"""

from .application_config import (
    Config,
    AudioConfig,
    VADConfig,
    STTServiceConfig,
    BufferConfig,
    ThreadingConfig,
    TranscriptConfig,
    get_config,
    SAMPLE_RATE,
    CHANNELS,
)

__all__ = [
    'Config',
    'AudioConfig',
    'VADConfig',
    'STTServiceConfig',
    'BufferConfig',
    'ThreadingConfig',
    'TranscriptConfig',
    'get_config',
    'SAMPLE_RATE',
    'CHANNELS',
]

