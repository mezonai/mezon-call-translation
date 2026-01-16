"""
Webhook API for handling LiveKit events.
Handles track_published/track_unpublished events and manages track egress for recording.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Optional

from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel

try:
    from livekit import api
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from dotenv import load_dotenv
load_dotenv()

from src.logger import get_logger
from src.api.webhook_auth import (
    verify_webhook,
    is_verification_enabled,
)

router = APIRouter()
logger = get_logger(__name__)

# Track các egress đã start để tránh duplicate
active_egresses: Dict[str, str] = {}


class WebhookResponse(BaseModel):
    """Webhook response model"""
    received: bool
    action: Optional[str] = None
    error: Optional[str] = None


class EgressInfo(BaseModel):
    """Egress info model"""
    egress_id: str
    track_sid: str
    room_name: str
    filepath: str
    started_at: str


def get_livekit_client() -> "api.LiveKitAPI":
    """Create LiveKit API client from environment variables."""
    url = os.getenv("LIVEKIT_HTTP_URL")
    api_key = os.getenv("LIVEKIT_API_KEY")
    api_secret = os.getenv("LIVEKIT_API_SECRET")
    
    if not api_key or not api_secret:
        raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")
    
    return api.LiveKitAPI(
        url=url,
        api_key=api_key,
        api_secret=api_secret
    )


async def start_track_recording(
    room_name: str, 
    track_sid: str, 
    track_type: str, 
    identity: str
) -> Optional[str]:
    """
    Start egress to record a track.
    
    Args:
        room_name: Name of the LiveKit room
        track_sid: Track SID to record
        track_type: Track type (AUDIO or VIDEO)
        identity: Participant identity
        
    Returns:
        Egress ID if successful, None otherwise
    """
    if not LIVEKIT_AVAILABLE:
        logger.error("LiveKit API not available")
        return None
    
    # Check if already recording this track
    if track_sid in active_egresses:
        logger.info(f"⏭ Track {track_sid} already being recorded, skipping")
        return active_egresses[track_sid]
    
    try:
        lk = get_livekit_client()
        # Create file output path
        recordings_dir = os.getenv("RECORDINGS_DIR", "/recordings")
        ext = "ogg" if track_type == "AUDIO" else "webm"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = f"{recordings_dir}/{room_name}-{identity}-{track_type.lower()}-{timestamp}.{ext}"
        
        file_out = api.DirectFileOutput(filepath=filepath)
        
        req = api.TrackEgressRequest(
            room_name=room_name,
            track_id=track_sid,
            file=file_out,
        )
        
        result = await lk.egress.start_track_egress(req)
        active_egresses[track_sid] = result.egress_id
        logger.info(f"✓ Started egress {result.egress_id}")
        logger.info(f"  File: {filepath}")
        
        await lk.aclose()
        return result.egress_id
        
    except Exception as e:
        logger.error(f"✗ Failed to start egress: {e}")
        return None


async def stop_track_recording(track_sid: str) -> bool:
    """
    Stop egress for a track.
    
    Args:
        track_sid: Track SID to stop recording
        
    Returns:
        True if stopped successfully, False otherwise
    """
    if track_sid not in active_egresses:
        logger.info(f"No active egress for track {track_sid}")
        return False
    
    try:
        lk = get_livekit_client()
        egress_id = active_egresses[track_sid]
        
        await lk.egress.stop_egress(api.StopEgressRequest(egress_id=egress_id))
        
        del active_egresses[track_sid]
        logger.info(f"✓ Stopped egress {egress_id} for track {track_sid}")
        
        await lk.aclose()
        return True
        
    except Exception as e:
        logger.error(f"✗ Failed to stop egress: {e}")
        return False


@router.get("/health")
async def health_check():
    """
    Health check endpoint.
    
    Returns:
        Service status and active egress count
    """
    return {
        "status": "ok",
        "service": "livekit-agent-webhook-handler",
        "livekit_available": LIVEKIT_AVAILABLE,
        "active_egresses_count": len(active_egresses),
        "timestamp": datetime.now().isoformat()
    }


@router.get("/egresses")
async def list_active_egresses():
    """
    List all active egresses being tracked.
    
    Returns:
        List of active egresses with their track SIDs
    """
    return {
        "status": "ok",
        "active_egresses": active_egresses,
        "count": len(active_egresses)
    }


@router.post("/webhook", response_model=WebhookResponse)
async def handle_webhook(request: Request):
    """
    Handle webhook events from LiveKit.
    
    Currently handles:
    - track_published: Start recording audio tracks
    - track_unpublished: Mark egress for cleanup
    - participant_joined: Log participant join
    - participant_left: Log participant leave
    - room_started: Log room start
    - room_finished: Log room finish
    
    Webhook verification:
    - Set LIVEKIT_VERIFY_WEBHOOKS=true (default) to enable signature verification
    - Set LIVEKIT_VERIFY_WEBHOOKS=false to skip verification (development only)
    """
    try:
        body = await request.body()
        body_str = body.decode("utf-8")
        auth_header = request.headers.get("Authorization", "")
        
        # Verify webhook signature if enabled
        if is_verification_enabled:
            is_valid, error = verify_webhook(body_str, auth_header)
            if not is_valid:
                logger.warning(f"⚠️ Webhook verification failed: {error}")
                raise HTTPException(status_code=401, detail=f"Webhook verification failed: {error}")
            logger.debug("✓ Webhook signature verified")
        else:
            logger.debug("⚠️ Webhook verification is disabled")
        
        event = json.loads(body_str)
        
        event_type = event.get("event", "unknown")
        logger.info(f"📥 Received webhook: {event_type}")
        
        # Log full event in debug mode
        logger.debug(f"Event payload: {json.dumps(event, indent=2, ensure_ascii=False)}")
        
        # Handle track_published
        if event_type == "track_published":
            room = event.get("room", {})
            participant = event.get("participant", {})
            track = event.get("track", {})
            
            room_name = room.get("name", "unknown")
            identity = participant.get("identity", "unknown")
            track_sid = track.get("sid", "")
            mime_type = track.get("mimeType", "")
            track_source = track.get("source", "UNKNOWN")
            
            is_audio = mime_type.startswith("audio")
            track_type = "AUDIO" if is_audio else "VIDEO"
            
            logger.info(f"  Room: {room_name}")
            logger.info(f"  Participant: {identity}")
            logger.info(f"  Track: {track_sid} (mime: {mime_type}, source: {track_source})")
            
            if is_audio:
                asyncio.create_task(
                    start_track_recording(room_name, track_sid, track_type, identity)
                )
                return WebhookResponse(received=True, action="recording_started")
            else:
                logger.info(f"  ⏭ Skipping track (recording disabled for {track_type})")
                return WebhookResponse(received=True, action=f"skipped_{track_type.lower()}")
        
        # Handle track_unpublished
        elif event_type == "track_unpublished":
            track = event.get("track", {})
            track_sid = track.get("sid", "")
            
            if track_sid in active_egresses:
                logger.info(f"  Track {track_sid} unpublished, egress should auto-stop")
                del active_egresses[track_sid]
                return WebhookResponse(received=True, action="egress_removed")
            
            return WebhookResponse(received=True, action="no_active_egress")
        
        # Handle participant events
        elif event_type == "participant_joined":
            participant = event.get("participant", {})
            room = event.get("room", {})
            logger.info(f"  Participant joined: {participant.get('identity', 'unknown')} in room {room.get('name', 'unknown')}")
            return WebhookResponse(received=True, action="participant_joined_logged")
        
        elif event_type == "participant_left":
            participant = event.get("participant", {})
            room = event.get("room", {})
            logger.info(f"  Participant left: {participant.get('identity', 'unknown')} from room {room.get('name', 'unknown')}")
            return WebhookResponse(received=True, action="participant_left_logged")
        
        # Handle room events
        elif event_type == "room_started":
            room = event.get("room", {})
            logger.info(f"  Room started: {room.get('name', 'unknown')}")
            return WebhookResponse(received=True, action="room_started_logged")
        
        elif event_type == "room_finished":
            room = event.get("room", {})
            logger.info(f"  Room finished: {room.get('name', 'unknown')}")
            return WebhookResponse(received=True, action="room_finished_logged")
        
        # Ignore other events
        else:
            logger.info(f"  (ignored)")
            return WebhookResponse(received=True, action="ignored")
        
    except json.JSONDecodeError as e:
        logger.error(f"✗ Invalid JSON in webhook body: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    except Exception as e:
        logger.error(f"✗ Error processing webhook: {e}")
        return WebhookResponse(received=False, error=str(e))


@router.post("/egress/stop/{track_sid}")
async def api_stop_egress(track_sid: str):
    """
    Manually stop egress for a specific track.
    
    Args:
        track_sid: The track SID to stop recording
        
    Returns:
        Status of the stop operation
    """
    if not LIVEKIT_AVAILABLE:
        raise HTTPException(status_code=503, detail="LiveKit API not available")
    
    if track_sid not in active_egresses:
        raise HTTPException(status_code=404, detail=f"No active egress for track {track_sid}")
    
    success = await stop_track_recording(track_sid)
    
    if success:
        return {"status": "stopped", "track_sid": track_sid}
    else:
        raise HTTPException(status_code=500, detail="Failed to stop egress")


@router.post("/egress/stop-all")
async def api_stop_all_egresses():
    """
    Stop all active egresses.
    
    Returns:
        Status of the stop operations
    """
    if not LIVEKIT_AVAILABLE:
        raise HTTPException(status_code=503, detail="LiveKit API not available")
    
    if not active_egresses:
        return {"status": "ok", "message": "No active egresses to stop", "stopped": 0}
    
    stopped = 0
    failed = 0
    track_sids = list(active_egresses.keys())  # Copy keys to avoid modification during iteration
    
    for track_sid in track_sids:
        success = await stop_track_recording(track_sid)
        if success:
            stopped += 1
        else:
            failed += 1
    
    return {
        "status": "ok",
        "stopped": stopped,
        "failed": failed,
        "remaining_egresses": len(active_egresses)
    }
