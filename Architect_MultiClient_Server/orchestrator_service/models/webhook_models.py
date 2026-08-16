from pydantic import BaseModel


class WebhookResponse(BaseModel):  # type: ignore[explicit-any]
    """Response model cho webhook"""

    received: bool
    action: str | None = None
    error: str | None = None


class TrackInfo(BaseModel):  # type: ignore[explicit-any]
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
