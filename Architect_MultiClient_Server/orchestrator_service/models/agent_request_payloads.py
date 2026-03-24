"""
Agent Request Payload Models

Pydantic models for different request type payloads with discriminated union.
"""

from typing import Literal, Union
from enum import Enum
from pydantic import BaseModel, Field
from orchestrator_service.models.agent_request_type import AgentRequestType


class TranscriptControlPayload(BaseModel):
    """Payload for transcript_control request"""
    request_type: Literal[AgentRequestType.TRANSCRIPT_CONTROL]
    action: Literal["enable", "disable"] = Field(..., description="Action to perform: enable or disable transcription")
    
    class Config:
        json_schema_extra = {
            "example": {
                "request_type": "transcript_control",
                "action": "enable"
            }
        }


class TtsPlayPayload(BaseModel):
    """Payload for tts_play request"""

    class VoiceEnum(str, Enum):
        """Supported Kokoro voices for TTS requests."""

        AF_HEART = "af_heart"
        AF_BELLA = "af_bella"
        AF_SARAH = "af_sarah"
        AM_ADAM = "am_adam"
        AM_MICHAEL = "am_michael"
        BF_EMMA = "bf_emma"
        BF_ISABELLA = "bf_isabella"
        BM_GEORGE = "bm_george"
        BM_LEWIS = "bm_lewis"

    request_type: Literal[AgentRequestType.TTS_PLAY]
    text: str = Field(..., description="Text to speak", min_length=1)
    sender_identity: str = Field(default="orchestrator", description="Identity of the sender")
    voice: VoiceEnum | None = Field(default=None, description="Optional voice name (e.g., af_heart)")
    speed: float | None = Field(default=None, description="Optional speech speed multiplier (0.5-2.0)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "request_type": "tts_play",
                "text": "Hello from orchestrator",
                "sender_identity": "orchestrator",
                "voice": "af_heart",
                "speed": 1.0
            }
        }


class SendChatMessagePayload(BaseModel):
    """Payload for send_chat_message request"""
    request_type: Literal[AgentRequestType.SEND_CHAT_MESSAGE]
    message: str = Field(..., description="Chat message to send", min_length=1)
    sender_name: str = Field(default="Agent", description="Display name of the sender")
    
    class Config:
        json_schema_extra = {
            "example": {
                "request_type": "send_chat_message",
                "message": "Hello from orchestrator!",
                "sender_name": "System Bot"
            }
        }


# Discriminated Union of all payload types
AgentRequestPayload = Union[
    TranscriptControlPayload,
    TtsPlayPayload,
    SendChatMessagePayload
]
