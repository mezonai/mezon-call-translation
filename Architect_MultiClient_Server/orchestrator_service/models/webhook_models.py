from typing import Optional, Dict
from pydantic import BaseModel
from typing import Optional, Dict, Any

class WebhookResponse(BaseModel):
    """Response model cho webhook"""
    received: bool
    action: Optional[str] = None
    error: Optional[str] = None


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
    """Egress information để gửi đi"""
    egressId: str
    room: Dict[str, str]
    participant: Dict[str, str]
    track: Dict[str, str]
    audio: Dict[str, Any]
    timeline: Dict[str, str]

