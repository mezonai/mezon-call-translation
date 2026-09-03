"""
Room Registry API - Manager active rooms for webhook processing
"""


from fastapi import APIRouter, Depends, HTTPException

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.room_registry_models import (
    RoomRegisterRequest,
    RoomRegisterResponse,
    RoomRegistryClearResponse,
    RoomRegistryListResponse,
    RoomStatusResponse,
    RoomUnregisterRequest,
    RoomUnregisterResponse,
)
from orchestrator_service.services.agents_bot_user_client import get_agents_bot_room_participants
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.asyncio_task_manager import asyncio_create_task_safety
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.transcript_validators import RoomNamePath

router = APIRouter(prefix="/room-registry", tags=["Room Registry"])
logger = get_logger(__name__)

# Initialize transcription service
transcription_service = TranscriptionService()


@router.post("/register", response_model=RoomRegisterResponse, response_description="Register a room for webhook processing")
async def register_room(
    request: RoomRegisterRequest,
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> RoomRegisterResponse:
    """Register a room in the registry so that webhooks can handle events for that room.
    **Example:**
    ```json
    {
        "room_name": "my-room-123",
        "room_id": "b3f1c2a4-4e5d-4a1b-9c3e-7a2f6d8e9c10"
    }
    ```
    """

    registry = get_room_registry()

    # 1. A new registration always supersedes whatever this room_name
    # currently points to (audio-ingestion PLAN.md D27x): Mezon's meeting
    # channels are a fixed, reused pool, so the *name* alone was never a
    # reliable identity -- the agent joining is what defines a session now.
    # An agent process crash+redispatch for the same LiveKit room is itself
    # defined as a new session here (same as a participant disconnecting and
    # rejoining), so there's no status/liveness check to make -- force-finalize
    # unconditionally, reusing the exact same finalize path a normal
    # end-of-call /unregister uses. That path's atomic
    # UPDATE ... WHERE status='pending' guard (final_room_status) is what
    # makes this safe if the superseded session's own delayed
    # cleanup/unregister call lands around the same time -- whichever call
    # actually matches the WHERE clause wins, the other is a no-op, not a race.
    current_room_id = await registry.get_room_id(request.room_name)
    if current_room_id and current_room_id != request.room_id:
        logger.warning(
            f"Room '{request.room_name}' re-registered (room_id={request.room_id}), "
            f"superseding room_id={current_room_id} -- force-finalizing it"
        )
        # try-except moved to final_room, not fatal -- proceed with the new registration regardless.
        await transcription_service.final_room(request.room_name, current_room_id)

    # 2. Create the room row for the agent's own id.
    if not await transcription_service.start_room(request.room_id, request.room_name):
        raise HTTPException(status_code=500, detail=f"Failed to create room record for '{request.room_name}'")

    # 3. Point the name -> id cache at this session (always overwrites).
    await registry.register_room(request.room_name, request.room_id)

    # 4. Save existing participants (best effort). Fetches the current
    # voice channel roster from agents-bot in the background so registration
    # latency remains minimal and non-blocking for the agent.
    asyncio_create_task_safety(_fetch_and_save_existing_participants(request.room_name, request.room_id))

    metadata_channel = MetadataChannel()
    asyncio_create_task_safety(metadata_channel.push_room_started(request.room_id, request.room_name))

    return RoomRegisterResponse(
        status="ok",
        message=f"Room '{request.room_name}' registered successfully",
        room_name=request.room_name,
        room_id=request.room_id,
    )


async def _fetch_and_save_existing_participants(room_name: str, room_id: str) -> None:
    """Fetch initial voice channel participants from agents-bot and persist to database."""
    try:
        participants = await get_agents_bot_room_participants(room_name)

        registry = get_room_registry()
        current_room_id = await registry.get_room_id(room_name)
        if current_room_id != room_id:
            logger.warning(
                f"Skip stale participant snapshot for room '{room_name}': "
                f"expected room_id={room_id}, current room_id={current_room_id}"
            )
            return

        if participants:
            saved = await transcription_service.save_participants_batch(room_id, participants)
            if saved:
                logger.info(
                    f"Saved {len(participants)} participants for room '{room_name}' (room_id={room_id})"
                )
            else:
                logger.error(
                    f"Failed to save participants for room '{room_name}' (room_id={room_id})"
                )
    except Exception as e:
        logger.error(
            f"Failed to fetch and save participants for room '{room_name}' (room_id={room_id}): {e}",
            exc_info=True,
        )


@router.post("/unregister", response_model=RoomUnregisterResponse, response_description="Unregister a room")
async def unregister_room(
    request: RoomUnregisterRequest,
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> RoomUnregisterResponse:
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

    asyncio_create_task_safety(transcription_service.final_room(request.room_name, room_id))

    metadata_channel = MetadataChannel()
    asyncio_create_task_safety(metadata_channel.push_room_ended(room_id, request.room_name))

    return RoomUnregisterResponse(
        status="ok",
        message=f"Room '{request.room_name}' unregistered successfully",
        room_name=request.room_name,
    )



@router.get("/status/{room_name}", response_model=RoomStatusResponse)
async def get_room_status(
    room_name: RoomNamePath,
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> RoomStatusResponse:
    """
    Check status registration for a room.

    Returns information about the room including room_id if the room is registered.
    """
    registry = get_room_registry()

    is_registered = await registry.is_registered(room_name)
    room_id = await registry.get_room_id(room_name) if is_registered else None

    return RoomStatusResponse(room_name=room_name, registered=is_registered, room_id=room_id)


@router.get("/list", response_model=RoomRegistryListResponse, response_description="List all registered rooms")
async def list_registered_rooms(
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> RoomRegistryListResponse:
    """
    Get a list of all currently registered rooms.

    Returns a dictionary with keys as room_name and values as room_id.
    """
    registry = get_room_registry()
    rooms = await registry.list_rooms()

    return RoomRegistryListResponse(
        status="ok",
        total=await registry.count_rooms(),
        rooms=rooms,
    )


@router.delete("/clear-all", response_model=RoomRegistryClearResponse, response_description="Clear all registered rooms")
async def clear_all_rooms(
    auth: dict[str, str | bool] = Depends(verify_api_key)
) -> RoomRegistryClearResponse:
    """
    clear all registered rooms from the registry.

    **Warning**: This action will delete all currently registered rooms.
    """
    registry = get_room_registry()
    cleared = await registry.clear_all()

    return RoomRegistryClearResponse(
        status="ok",
        message=f"Cleared {cleared} rooms from registry",
        cleared_count=cleared,
    )
