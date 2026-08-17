"""
WebSocket clients for external services

This module provides WebSocket clients for:
- STT (Speech-to-Text): Nemotron transcription server
- TTS (Text-to-Speech): TTS generation server

All clients inherit from BaseWebSocketClient for common functionality.
"""

from .base_client import BaseWebSocketClient
from .stt_client import STTWebSocketClient


__all__ = [
    "BaseWebSocketClient",
    "STTWebSocketClient",
]
