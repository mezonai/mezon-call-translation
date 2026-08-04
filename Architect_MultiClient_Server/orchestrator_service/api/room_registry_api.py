"""
Room Registry API - Manager active rooms for webhook processing
"""

import asyncio
from typing import Optional
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel

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
    stt_room_id = None

    # 1. Start room in STT service FIRST
    try:
        stt_response = await transcription_service.start_room(request.room_name)
        if stt_response:
            if stt_response.get("success"):
                stt_room_id = stt_response.get("room_id")
                logger.info(f"✅ Room '{request.room_name}' started in STT service")
        else:
            logger.warning(f"⚠️ Failed to start room in STT service")
    except Exception as e:
        logger.error(f"Error starting room in STT: {e}", exc_info=True)

    # 2. Register room in registry
    if stt_room_id is None:
        raise HTTPException(
            status_code=400, detail=f"Failed to obtain room_id from STT service"
        )
    if not await registry.register_room(request.room_name, stt_room_id):
        logger.error(f"Room '{request.room_name}' is already registered")
        raise HTTPException(
            status_code=409, detail=f"Room '{request.room_name}' is already registered"
        )

    # Recording itself is driven by agents/record-service once the agent
    # joins and subscribes tracks (audio-ingestion PLAN.md D3) -- orchestrator
    # no longer needs to kick off egress for already-published tracks here.

    metadata_channel = MetadataChannel()
    asyncio.create_task(
        metadata_channel.push_room_started(str(stt_room_id), request.room_name)
    )

    return {
        "status": "ok",
        "message": f"Room '{request.room_name}' registered successfully",
        "room_name": request.room_name,
        "room_id": str(stt_room_id),
    }


@router.post("/unregister", response_description="Unregister a room")
async def unregister_room(
    request: RoomUnregisterRequest,
):
    """
    Unregister a room from the registry.

    After unregistering, the webhook will no longer process events for this room.
    Additionally finalizes room status in the STT service (which also drives
    the recording-derivative lifecycle, audio-ingestion PLAN.md D18/D19 --
    record-service/agents stop recording independently once the agent leaves
    the room, not because of this call).

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
                detail=f"Room '{request.room_name}' not found in registry",
            )

        # Unregister room from registry
        if not await registry.unregister_room(request.room_name):
            raise HTTPException(
                status_code=404,
                detail=f"Room '{request.room_name}' not found in registry",
            )

        try:
            asyncio.create_task(
                transcription_service.final_room(request.room_name, room_id)
            )
        except Exception as e:
            logger.error(
                f"Error finalizing room '{request.room_name}': {e}", exc_info=True
            )
            # Don't fail unregistration if finalization fails

        metadata_channel = MetadataChannel()
        asyncio.create_task(
            metadata_channel.push_room_ended(str(room_id), request.room_name)
        )

        return {
            "status": "ok",
            "message": f"Room '{request.room_name}' unregistered successfully",
            "room_name": request.room_name,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unregistering room: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to unregister room: {str(e)}"
        )


@router.get("/status/{room_name}", response_model=RoomStatusResponse)
async def get_room_status(
    room_name: str,
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
            room_name=room_name, registered=is_registered, room_id=room_id
        )
    except Exception as e:
        logger.error(f"Error getting room status: {e}", exc_info=True)
        raise HTTPException(
            status_code=500, detail=f"Failed to get room status: {str(e)}"
        )


@router.get("/list", response_description="List all registered rooms")
async def list_registered_rooms():
    """
    Get a list of all currently registered rooms.

    Returns a dictionary with keys as room_name and values as room_id.
    """
    try:
        registry = get_room_registry()
        rooms = await registry.list_rooms()

        return {"status": "ok", "total": await registry.count_rooms(), "rooms": rooms}
    except Exception as e:
        logger.error(f"Error listing rooms: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to list rooms: {str(e)}")


@router.delete("/clear-all", response_description="Clear all registered rooms")
async def clear_all_rooms():
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
            "cleared_count": cleared,
        }
    except Exception as e:
        logger.error(f"Error clearing rooms: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to clear rooms: {str(e)}")
