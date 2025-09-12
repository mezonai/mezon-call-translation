"""Constants configuration for the application"""

import os
from typing import Dict, Any

# Audio configuration
SAMPLE_RATE: int = int(os.getenv('AUDIO_SAMPLE_RATE', '16000'))
CHANNELS: int = int(os.getenv('AUDIO_CHANNELS', '1'))

# Service URLs
TRANSCRIPT: str = True
TRANSLATION: str = False

# Default configuration
DEFAULT_CONFIG: Dict[str, Any] = {
    'sample_rate': SAMPLE_RATE,
    'channels': CHANNELS,
    'transcript_url': TRANSCRIPT,
    'translation_url': TRANSLATION
}
