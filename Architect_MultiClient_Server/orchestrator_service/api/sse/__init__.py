"""
SSE (Server-Sent Events) Base Package
Provides generic SSE infrastructure for multiple event channels
"""

from .sse_base import create_sse_response, event_generator
from .sse_manager import SSEManager

__all__ = [
    "SSEManager",
    "create_sse_response",
    "event_generator",
]
