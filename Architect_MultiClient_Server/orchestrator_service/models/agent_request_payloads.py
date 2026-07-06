"""
Agent Request Payload Models

Pydantic models for different request type payloads with discriminated union.
"""

from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from orchestrator_service.models.agent_request_type import AgentRequestType


class TranscriptControlPayload(BaseModel):                      # type: ignore[explicit-any]
    """Payload for transcript_control request"""

    request_type: Literal[AgentRequestType.TRANSCRIPT_CONTROL]
    action: Literal["enable", "disable"] = Field(..., description="Action to perform: enable or disable transcription")

    class Config:
        json_schema_extra: ClassVar[dict[str, Any]] = {"example": {"request_type": "transcript_control", "action": "enable"}}


class TtsPlayPayload(BaseModel):                                # type: ignore[explicit-any]
    """Payload for tts_play request"""

    class VoiceEnum(StrEnum):
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
        json_schema_extra: ClassVar[dict[str, Any]] = {
            "example": {
                "request_type": "tts_play",
                "text": "Hello from orchestrator",
                "sender_identity": "orchestrator",
                "voice": "af_heart",
                "speed": 1.0,
            }
        }


class SendChatMessagePayload(BaseModel):                       # type: ignore[explicit-any]
    """Payload for send_chat_message request"""

    request_type: Literal[AgentRequestType.SEND_CHAT_MESSAGE]
    message: str = Field(..., description="Chat message to send", min_length=1)
    sender_name: str = Field(default="Agent", description="Display name of the sender")

    class Config:
        json_schema_extra: ClassVar[dict[str, Any]] = {
            "example": {
                "request_type": "send_chat_message",
                "message": "Hello from orchestrator!",
                "sender_name": "System Bot",
            }
        }


class StartAudioRecordingPayload(BaseModel):                    # type: ignore[explicit-any]
    """Payload for start_audio_recording request"""

    request_type: Literal[AgentRequestType.START_AUDIO_RECORDING]
    track_id: str = Field(..., description="LiveKit track ID to record (e.g., from a TrackPublished event)")
    file_output_path: str = Field(
        ..., description="File path to save the recorded audio (e.g., s3://bucket/recordings/agent123_track456.wav)"
    )

    class Config:
        json_schema_extra: ClassVar[dict[str, Any]] = {
            "example": {
                "request_type": "start_audio_recording",
                "track_id": "livekit_track_id_123",
                "file_output_path": "s3://my-bucket/recordings/agent123_track456.wav",
            }
        }


# Discriminated Union of all payload types
AgentRequestPayload = (
    TranscriptControlPayload | TtsPlayPayload | SendChatMessagePayload | StartAudioRecordingPayload
)
