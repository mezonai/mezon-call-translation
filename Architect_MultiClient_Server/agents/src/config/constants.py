"""Constants configuration for the application"""

import os

# Audio configuration constants
SAMPLE_RATE: int = int(os.getenv('AUDIO_SAMPLE_RATE', '16000'))
CHANNELS: int = int(os.getenv('AUDIO_CHANNELS', '1'))

