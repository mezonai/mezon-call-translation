"""
Room Registry API - Manager active rooms for webhook processing
"""
import asyncio
from datetime import datetime

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from bson import ObjectId
from livekit import api

from orchestrator_service.utils.participant_identity import parse_participant_identity
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.api.webhook_api import egress_service

router = APIRouter(prefix="/room-registry", tags=["Room Registry"])
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
            }
        }


class RoomUnregisterRequest(BaseModel):
    """Request model for room unregistration"""
    room_name: str = Field(..., description="Room name to unregister")


class RoomStatusResponse(BaseModel):
    """Response model for room status"""
    room_name: str
    registered: bool
    room_id: Optional[str] = None

@router.post("/register", response_description="Register a room for webhook processing")
async def register_room(
    request: RoomRegisterRequest,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """Register a room in the registry so that webhooks can handle events for that room.
    **Example:**
    ```json
    {
        "room_name": "my-room-123"
    }
    ```
    """
    
    registry = get_room_registry()
    room_id = None
    tracks_started = 0

    # 1. Start room in STT service FIRST
    try:
        stt_response = await transcription_service.start_room(request.room_name)
        if stt_response:
            if stt_response.get("success"):
                room_id = stt_response.get("room_id")
                logger.info(f"✅ Room '{request.room_name}' started in STT service")
        else:
            logger.warning(f"⚠️ Failed to start room in STT service")
    except Exception as e:
        logger.error(f"Error starting room in STT: {e}", exc_info=True)
    
    # 2. Register room in registry
    if room_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"Failed to obtain room_id from STT service"
        )
    
    if not await registry.register_room(request.room_name, room_id):
        raise HTTPException(
            status_code=409,
            detail=f"Room '{request.room_name}' is already registered"
        )
    
    # 3. Start recording for existing tracks (best effort)
    try:
        livekit_service = get_livekit_service()
        if livekit_service.is_available:
            client = livekit_service.get_client()
            
            participants_response = await client.room.list_participants(
                api.ListParticipantsRequest(room=request.room_name)
            )
            
            logger.info(f"Found {len(participants_response.participants)} participants")
            participants_data = []
            for participant in participants_response.participants:
                participants_data.append({
                    "participant_identity": participant.identity,
                    "timestamp": datetime.utcnow()
                })
                for track in participant.tracks:
                    # Check if audio track
                    is_audio = track.type == 0 or track.source == 4
                    if is_audio:
                        source_str = {
                            4: "SCREEN_SHARE_AUDIO",
                            2: "MICROPHONE"
                        }.get(track.source, "UNKNOWN")
                        
                        participant_identity = parse_participant_identity(participant.identity)

                        logger.info(
                            f"Starting recording: track={track.sid}, "
                            f"participant={participant_identity}, source={source_str}"
                        )
                        
                        asyncio.create_task(
                            egress_service.start_recording(
                                request.room_name,
                                track.sid,
                                "AUDIO",
                                source_str,
                                participant_identity
                            )
                        )
                        tracks_started += 1

            # Save all participants at once
            if participants_data:
                await transcription_service.save_participants_batch(room_id, participants_data)


            logger.info(f"Started {tracks_started} audio track recordings")
        else:
            logger.warning("LiveKit API not available")
    
    except Exception as e:
        logger.error(f"Error setting up recordings: {e}", exc_info=True)
        # Continue - room is already registered
    
    metadata_channel = MetadataChannel()
    asyncio.create_task(metadata_channel.push_room_started(str(room_id), request.room_name))
    
    return {
        "status": "ok",
        "message": f"Room '{request.room_name}' registered successfully",
        "room_name": request.room_name,
        "room_id": str(room_id),
        "tracks_started": tracks_started
    }       



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
        
        # Get room_id from registry
        room_id = await registry.get_room_id(request.room_name)
        if not room_id:
            raise HTTPException(
                status_code=404,
                detail=f"Room '{request.room_name}' not found in registry"
            )
        
        # Unregister room from registry
        if not await registry.unregister_room(request.room_name):
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
        
        try:
            asyncio.create_task(transcription_service.final_room(
                request.room_name, 
                room_id
            ))
        except Exception as e:
            logger.error(f"Error finalizing room '{request.room_name}': {e}", exc_info=True)
            # Don't fail unregistration if finalization fails
        
        metadata_channel = MetadataChannel()
        asyncio.create_task(metadata_channel.push_room_ended(room_id, request.room_name))

        return {
            "status": "ok",
            "message": f"Room '{request.room_name}' unregistered successfully",
            "room_name": request.room_name,
            "egresses_stopped": egress_result["stopped"],
            "egresses_failed": egress_result["failed"],
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
    
    Returns information about the room including room_id if the room is registered.
    """
    try:
        registry = get_room_registry()
        
        is_registered = await registry.is_registered(room_name)
        room_id = await registry.get_room_id(room_name) if is_registered else None
        
        return RoomStatusResponse(
            room_name=room_name,
            registered=is_registered,
            room_id=room_id
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
    
    Returns a dictionary with keys as room_name and values as room_id.
    """
    try:
        registry = get_room_registry()
        rooms = await registry.list_rooms()
        
        return {
            "status": "ok",
            "total": await registry.count_rooms(),
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
        count = await registry.count_rooms()
        cleared = await registry.clear_all()
        
        return {
            "status": "ok",
            "message": f"Cleared {cleared} rooms from registry",
            "cleared_count": cleared
        }
    except Exception as e:
        logger.error(f"Error clearing rooms: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to clear rooms: {str(e)}"
        )
