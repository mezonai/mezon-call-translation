"""
Agent Request Type Enum

Defines the types of requests that can be sent from orchestrator to agent via SSE.
"""

from enum import Enum


class AgentRequestType(str, Enum):
    """
    Enum for agent request types.
    """
    
    TRANSCRIPT_CONTROL = "transcript_control"
    """Control transcription (enable/disable)"""
    
    TTS_PLAY = "tts_play"
    """Play TTS audio"""
    
    SEND_CHAT_MESSAGE = "send_chat_message"
    """Send chat message to room participants"""
    
    @classmethod
    def values(cls) -> list[str]:
        """Get all enum values as strings."""
        return [e.value for e in cls]
    
    @classmethod
    def has_value(cls, value: str) -> bool:
        """Check if value is a valid request type."""
        return value in cls.values()
