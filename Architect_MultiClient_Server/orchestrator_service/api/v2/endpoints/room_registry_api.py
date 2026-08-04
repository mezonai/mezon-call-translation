"""
Room Registry API - Manager active rooms for webhook processing
"""

import asyncio
from datetime import datetime

from typing import Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from livekit import api

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel

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
    room_id: Optional[str] = Field(
        None,
        description=(
            "Caller's own stable room UUID from registration (audio-ingestion "
            "PLAN.md D27). If given, the registry entry for room_name is only "
            "cleared when it still points to this exact room_id -- protects "
            "against a late unregister clobbering a newer registration that "
            "reused the same room_name in the meantime."
        ),
    )


class RoomStatusResponse(BaseModel):
    """Response model for room status"""

    room_name: str
    registered: bool
    room_id: Optional[str] = None


@router.post("/register", response_description="Register a room for webhook processing")
async def register_room(
    request: RoomRegisterRequest, auth: Dict[str, Any] = Depends(verify_api_key)
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
            status_code=400, detail=f"Failed to obtain room_id from STT service"
        )

    if not await registry.register_room(request.room_name, room_id):
        raise HTTPException(
            status_code=409, detail=f"Room '{request.room_name}' is already registered"
        )

    # 3. Save existing participants (best effort). Recording itself is driven
    # by agents/record-service once the agent joins and subscribes tracks
    # (audio-ingestion PLAN.md D3) -- no egress kick-off needed here anymore.
    # Backgrounded (audio-ingestion PLAN.md D27): the LiveKit list_participants
    # API call is the single biggest source of latency in this endpoint, and
    # the caller (agent, registering *before* connecting to the room -- see
    # main.py) doesn't need it to be done before getting room_id back. Not a
    # correctness downgrade: this is still a live query against LiveKit made
    # right after the registry entry goes active, same as before, just not
    # blocking the response -- the participant_joined webhook (active from
    # step 2 onward, same as before) still catches anyone who joins around
    # this same window.
    asyncio.create_task(_fetch_and_save_existing_participants(request.room_name, room_id))

    metadata_channel = MetadataChannel()
    asyncio.create_task(
        metadata_channel.push_room_started(str(room_id), request.room_name)
    )

    return {
        "status": "ok",
        "message": f"Room '{request.room_name}' registered successfully",
        "room_name": request.room_name,
        "room_id": str(room_id),
    }


async def _fetch_and_save_existing_participants(room_name: str, room_id: str) -> None:
    """Background half of register_room's step 3 -- see call site comment."""
    try:
        livekit_service = get_livekit_service()
        if not livekit_service.is_available:
            logger.warning("LiveKit API not available")
            return

        client = livekit_service.get_client()
        participants_response = await client.room.list_participants(
            api.ListParticipantsRequest(room=room_name)
        )

        logger.info(f"Found {len(participants_response.participants)} participants")
        participants_data = [
            {
                "participant_identity": participant.identity,
                "timestamp": datetime.utcnow(),
            }
            for participant in participants_response.participants
        ]

        if participants_data:
            await transcription_service.save_participants_batch(room_id, participants_data)
    except Exception as e:
        logger.error(f"Error saving existing participants for room '{room_name}': {e}", exc_info=True)


@router.post("/unregister", response_description="Unregister a room")
async def unregister_room(
    request: RoomUnregisterRequest, auth: Dict[str, Any] = Depends(verify_api_key)
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

        # Whichever registration currently owns this room_name right now
        # (may differ from the caller's own room_id -- see below).
        current_room_id = await registry.get_room_id(request.room_name)

        # Prefer the caller's own room_id (audio-ingestion PLAN.md D27) --
        # a worker captures this once at its own registration and it never
        # changes for that worker's lifetime, unlike re-resolving by name
        # here, which can point to a *different* room if room_name was
        # already reused by a new call by the time this request lands.
        room_id = request.room_id or current_room_id
        if not room_id:
            raise HTTPException(
                status_code=404,
                detail=f"Room '{request.room_name}' not found in registry",
            )

        if current_room_id == room_id:
            # Registry still points to our own registration -- safe to clear.
            if not await registry.unregister_room(request.room_name):
                raise HTTPException(
                    status_code=404,
                    detail=f"Room '{request.room_name}' not found in registry",
                )
        elif current_room_id is not None:
            # room_name has already been re-registered under a different
            # room_id (a new call reusing the same name) -- do NOT touch
            # the registry, it belongs to that new call now. Still finalize
            # *our* room below, by its own stable id, since that's unrelated
            # to whoever currently owns the name.
            logger.warning(
                f"Unregister for room '{request.room_name}' (room_id={room_id}) arrived "
                f"after the name was reused by room_id={current_room_id} -- "
                f"leaving the registry entry alone, finalizing our room only"
            )
        # else current_room_id is None: already unregistered (e.g. a retried
        # call) -- nothing to clear, just proceed to finalize by room_id.

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
    room_name: str, auth: Dict[str, Any] = Depends(verify_api_key)
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
async def list_registered_rooms(auth: Dict[str, Any] = Depends(verify_api_key)):
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
async def clear_all_rooms(auth: Dict[str, Any] = Depends(verify_api_key)):
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
