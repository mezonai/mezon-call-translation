"""
Transcription API Controller

Provides REST endpoints for transcription internal operations.
Note: Queue monitoring endpoints (stats, task status) are now in orchestrator_service.
"""

import logging
from fastapi import APIRouter
from pydantic import BaseModel

from stt_service.service.mongodb_service import MongoDBService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/transcribe", tags=["transcription"])


class RoomInfo(BaseModel):
    name: str
    room_id: str

class SessionInfo(BaseModel):
    room_name: str

class TrackMetadataRequest(BaseModel):
    """Request model for saving track metadata."""
    egress_id: str
    track_id: str
    room_ref_id: str
    participant_identity: str

