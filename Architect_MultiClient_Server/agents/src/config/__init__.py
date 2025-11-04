"""Configuration package"""

from .audio_config import (
    AudioProcessorConfig,
    AudioThreadingConfig,
    VadConfig,
    AudioConfigError
)

from .constants import (
    SAMPLE_RATE,
    CHANNELS
)

__all__ = [
    'AudioProcessorConfig',
    'AudioThreadingConfig',
    'VadConfig',
    'AudioConfigError',
    'SAMPLE_RATE',
    'CHANNELS'
]

