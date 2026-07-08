from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WebhookResponse(BaseModel):           # type: ignore[explicit-any]
    """Response model cho webhook"""

    received: bool
    action: str | None = None
    error: str | None = None


class TrackInfo(BaseModel):                 # type: ignore[explicit-any]
    """Track information từ webhook event"""

    sid: str
    mime_type: str
    source: str

    @property
    def is_audio(self) -> bool:
        return self.mime_type.startswith("audio")

    @property
    def track_type(self) -> str:
        return "AUDIO" if self.is_audio else "VIDEO"


class EgressInfo(BaseModel):                # type: ignore[explicit-any]
    """Egress information để gửi đi (simplified to match TranscriptionRequest)"""

    egress_id: str
    filename: str
    location: str
    duration: str
    started_at: str
    ended_at: str
    source: str | None = None

# ==========================================
# LiveKit Webhook Payload Models
# ==========================================

class WebhookRoom(BaseModel):               # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")
    name: str | None = None


class WebhookParticipant(BaseModel):        # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")
    identity: str | None = None
    disconnect_reason: str | None = None


class WebhookTrack(BaseModel):              # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")
    sid: str | None = None
    mime_type: str | None = None
    source: str | None = None


class WebhookEgressFile(BaseModel):         # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")
    filename: str | None = None
    filepath: str | None = None
    location: str | None = None
    duration: int | str | None = None
    started_at: int | str | None = None
    ended_at: int | str | None = None


class WebhookEgressInfo(BaseModel):         # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")
    egress_id: str | None = None
    room_name: str | None = None
    status: str | None = None
    error: str | None = None
    file: WebhookEgressFile | None = None
    # TODO: Use Any because webhook track payload structure varies dynamically
    track: dict[str, Any] | None = None     # type: ignore[explicit-any]


class LiveKitWebhookEvent(BaseModel):       # type: ignore[explicit-any]
    model_config = ConfigDict(extra="ignore")

    event: str | None = Field(default="unknown")
    room: WebhookRoom | None = None
    participant: WebhookParticipant | None = None
    track: WebhookTrack | None = None
    egress_info: WebhookEgressInfo | None = None
