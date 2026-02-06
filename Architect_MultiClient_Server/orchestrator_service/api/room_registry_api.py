"""
Room Registry API - Manager active rooms for webhook processing
"""
import asyncio
from datetime import datetime
from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from livekit import api
import time

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.auth.transcript_auth import verify_api_key

# Import để có thể access egress_service
from orchestrator_service.api.webhook_api import egress_service

router = APIRouter(prefix="/api/room-registry", tags=["Room Registry"])
logger = get_logger(__name__)

# Initialize transcription service
transcription_service = TranscriptionService()


class RoomRegisterRequest(BaseModel):
    """Request model for room registration"""
    room_name: str = Field(..., description="Room name to register")
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_name": "my-room-123",
                "start_time": None
            }
        }


class RoomUnregisterRequest(BaseModel):
    """Request model for room unregistration"""
    room_name: str = Field(..., description="Room name to unregister")


class RoomStatusResponse(BaseModel):
    """Response model for room status"""
    room_name: str
    registered: bool
    start_time: Optional[float] = None
    duration: Optional[float] = None


@router.post("/register", response_description="Register a room for webhook processing")
async def register_room(
    request: RoomRegisterRequest,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Register a room in the registry so that webhooks can handle events for that room.

    Only registered rooms can process webhook events.
    
    **Example:**
    ```json
    {
        "room_name": "my-room-123",
    }
    ```
    """
    try:
        registry = get_room_registry()
        
        # If not provided, use the current time
        actual_start_time = datetime.utcnow().strftime("%Y%m%d_%H%M%S")

        # Register room in registry 
        if not registry.register_room(request.room_name, actual_start_time):
            raise HTTPException(
                status_code=409,
                detail=f"Room '{request.room_name}' is already registered"
            )
        # get all tracks in the room and start recording for audio tracks
        tracks_started = 0
        try:
            livekit_service = get_livekit_service()
            if livekit_service.is_available:
                client = livekit_service.get_client()
                
                # List all participants in room
                participants_response = await client.room.list_participants(
                    api.ListParticipantsRequest(room=request.room_name)
                )
                
                logger.info(f"Found {len(participants_response.participants)} participants in room '{request.room_name}'")
                
                # Iterate through each participant
                for participant in participants_response.participants:
                    identity = participant.identity
                    logger.info(f"Processing participant: {identity}")
                    
                    # Iterate through tracks of participant
                    for track in participant.tracks:
                        track_sid = track.sid
                        track_type = track.type  # 0=AUDIO, 1=VIDEO, 2=DATA
                        source = track.source  # 0=UNKNOWN, 1=CAMERA, 2=MICROPHONE, 3=SCREEN_SHARE, 4=SCREEN_SHARE_AUDIO
                        
                        # Check if it's an audio track (AUDIO type or SCREEN_SHARE_AUDIO source)
                        is_audio = track_type == 0 or source == 4
                        
                        if is_audio:
                            # Determine track type and source string
                            if source == 4:  # Screen share audio
                                track_type_str = "AUDIO"
                                source_str = "SCREEN_SHARE_AUDIO"
                            elif source == 2:  # Microphone
                                track_type_str = "AUDIO"
                                source_str = "MICROPHONE"
                            else:
                                track_type_str = "AUDIO"
                                source_str = "UNKNOWN"
                            
                            logger.info(f"Starting recording for track {track_sid} (participant: {identity}, source: {source_str})")
                            
                            # Start recording asynchronously
                            asyncio.create_task(
                                egress_service.start_recording(
                                    request.room_name,
                                    track_sid,
                                    track_type_str,
                                    source_str,
                                    identity
                                )
                            )
                            tracks_started += 1
                        else:
                            logger.debug(f"Skipping non-audio track {track_sid} (type: {track_type}, source: {source})")
                
                logger.info(f"Started recording for {tracks_started} audio tracks in room '{request.room_name}'")
            else:
                logger.warning("LiveKit API not available, skipping track recording setup")
        
        except Exception as e:
            logger.error(f"Error setting up recordings for existing tracks: {e}", exc_info=True)
            # Don't fail registration if track recording fails
            logger.warning("Room registered but some tracks may not be recording")
        
        return {
            "status": "ok",
            "message": f"Room '{request.room_name}' registered successfully",
            "room_name": request.room_name,
            "start_time": registry.get_room_start_time(request.room_name),
            "tracks_started": tracks_started
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error registering room: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to register room: {str(e)}"
        )


@router.post("/unregister", response_description="Unregister a room")
async def unregister_room(
    request: RoomUnregisterRequest,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Unregister a room from the registry.
    
    After unregistering, the webhook will no longer process events for this room.
    Additionally:
    - Stop all running egress recordings
    - Finalize room status in the STT service
    
    **Example:**
    ```json
    {
        "room_name": "my-room-123"
    }
    ```
    """
    try:
        registry = get_room_registry()
        
        # get start_session_time after unregister (ISO string)
        start_session_time = registry.get_room_start_time(request.room_name)
        
        # Unregister room from registry
        if not registry.unregister_room(request.room_name):
            raise HTTPException(
                status_code=404,
                detail=f"Room '{request.room_name}' not found in registry"
            )
        
        # Stop all active egress recordings for this room
        egress_result = {"stopped": 0, "failed": 0}
        try:
            egress_result = await egress_service.stop_all_by_room(request.room_name)
        except Exception as e:
            logger.error(f"Error stopping egresses for room '{request.room_name}': {e}", exc_info=True)
            # Don't fail unregistration if egress stopping fails
        
        # Finalize room in STT service
        final_room_success = False
        try:
            final_room_success = await transcription_service.final_room(
                request.room_name, 
                start_session_time
            )
            if final_room_success:
                logger.info(f"✅ Room '{request.room_name}' finalized in STT service")
            else:
                logger.warning(f"⚠️ Failed to finalize room '{request.room_name}' in STT service")
        except Exception as e:
            logger.error(f"Error finalizing room '{request.room_name}': {e}", exc_info=True)
            # Don't fail unregistration if finalization fails
        
        return {
            "status": "ok",
            "message": f"Room '{request.room_name}' unregistered successfully",
            "room_name": request.room_name,
            "egresses_stopped": egress_result["stopped"],
            "egresses_failed": egress_result["failed"],
            "room_finalized": final_room_success
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unregistering room: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to unregister room: {str(e)}"
        )



@router.get("/status/{room_name}", response_model=RoomStatusResponse)
async def get_room_status(
    room_name: str,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Check status registration for a room.
    
    Returns information about the room including start_time and duration if the room is registered.
    """
    try:
        registry = get_room_registry()
        
        is_registered = registry.is_registered(room_name)
        start_time = registry.get_room_start_time(room_name)
        duration = registry.get_room_duration(room_name)
        
        return RoomStatusResponse(
            room_name=room_name,
            registered=is_registered,
            start_time=start_time,
            duration=duration
        )
    except Exception as e:
        logger.error(f"Error getting room status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get room status: {str(e)}"
        )


@router.get("/list", response_description="List all registered rooms")
async def list_registered_rooms(
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get a list of all currently registered rooms.
    
    Returns a dictionary with keys as room_name and values as start_time.
    """
    try:
        registry = get_room_registry()
        rooms = registry.list_rooms()
        
        return {
            "status": "ok",
            "total": registry.count_rooms(),
            "rooms": rooms
        }
    except Exception as e:
        logger.error(f"Error listing rooms: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list rooms: {str(e)}"
        )


@router.delete("/clear-all", response_description="Clear all registered rooms")
async def clear_all_rooms(
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    clear all registered rooms from the registry.
    
    **Warning**: This action will delete all currently registered rooms.
    """
    try:
        registry = get_room_registry()
        count = registry.count_rooms()
        registry.clear_all()
        
        return {
            "status": "ok",
            "message": f"Cleared {count} rooms from registry",
            "cleared_count": count
        }
    except Exception as e:
        logger.error(f"Error clearing rooms: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear rooms: {str(e)}"
        )
