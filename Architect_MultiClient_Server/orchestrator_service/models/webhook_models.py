from pydantic import BaseModel


class WebhookResponse(BaseModel):
    """Response model cho webhook"""

    received: bool
    action: str | None = None
    error: str | None = None


class TrackInfo(BaseModel):
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


class EgressInfo(BaseModel):
    """Egress information để gửi đi (simplified to match TranscriptionRequest)"""

    egressId: str
    filename: str
    location: str
    duration: str
    startedAt: str
    endedAt: str
    source: str | None = None
