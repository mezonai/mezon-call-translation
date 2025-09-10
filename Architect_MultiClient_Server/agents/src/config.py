import os
from dotenv import load_dotenv

load_dotenv()

# WebSocket Server configuration (Vosk-style)
WEBSOCKET_HOST = os.getenv("WEBSOCKET_HOST", "localhost")
WEBSOCKET_PORT = int(os.getenv("WEBSOCKET_PORT", "8000"))
TRANSCRIPT = True
TRANSLATION = True

SAMPLE_RATE = 16000
CHANNELS = 1

# Optimization parameters
BATCH_SIZE = 1  # Number of frames to batch before sending
SEND_DELAY = 0.005  # 5ms delay between sends to prevent server overload
MAX_BUFFER_SIZE = 1024 * 16  # 16KB max buffer size
RECONNECT_MAX_ATTEMPTS = 3
RECONNECT_BASE_DELAY = 1.0