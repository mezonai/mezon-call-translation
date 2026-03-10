"""
Agent Request Payload Models

Pydantic models for different request type payloads with discriminated union.
"""

from typing import Literal, Union
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
    request_type: Literal[AgentRequestType.TTS_PLAY]
    text: str = Field(..., description="Text to speak", min_length=1)
    sender_identity: str = Field(default="orchestrator", description="Identity of the sender")
    
    class Config:
        json_schema_extra = {
            "example": {
                "request_type": "tts_play",
                "text": "Hello from orchestrator",
                "sender_identity": "orchestrator"
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
