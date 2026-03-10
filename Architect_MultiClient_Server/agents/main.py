"""
LiveKit Agent entrypoint - Starts Vosk transcription agent + TTS
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from livekit import agents, api

from src.core.transcript_manager import TranscriptManager
from src.core.event_handlers import EventHandlers
from src.core.agent_control_state import AgentControlState
from src.core.datachannel_dispatcher import DataChannelDispatcher
from src.core.lifecycle_manager import (
    initialize_tts_manager,
    register_with_orchestrator,
    start_agent_request_listener,
    create_cleanup_callback,
    subscribe_existing_tracks
)
from src.logger import get_logger
from src.config.application_config import get_config
from src.services.orchestrator_client import OrchestratorClient

# Load config
config = get_config()
logger = get_logger(__name__)


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup and lifecycle management"""
    logger.info(f"✅ Connecting to room: {ctx.room.name}")
    
    # Initialize orchestrator client singleton
    orchestrator = OrchestratorClient.get_instance()
    
    # Create a new token to change the identity displayed in the room
    new_token = api.AccessToken(config.livekit.api_key, config.livekit.api_secret)
    new_token.with_identity(config.livekit.agent_name)
    new_token.with_name("KOMU Agent")
    new_token.with_grants(api.VideoGrants(
        room_join=True,
        room=ctx.room.name,
        can_publish=True,
        can_subscribe=True
    ))
    new_token.with_kind("agent")
    ctx._info.token = new_token.to_jwt()
    
    # Setup core components
    session_id = ctx.room.name
    transcript_manager = TranscriptManager(ctx)
    control_state = AgentControlState(transcription_enabled=False)
    event_handlers = EventHandlers(ctx, transcript_manager, control_state=control_state)
    
    # Register event handlers
    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
    
    # Initialize TTS Manager (optional)
    tts_manager = await initialize_tts_manager(ctx, session_id)
    
    # Connect to room
    await ctx.connect()
    p = ctx.room.local_participant
    
    logger.info(
        f"[AGENT STARTED] "
        f"identity={p.identity} | "
        f"name={p.name} | "
        f"sid={p.sid} | "
        f"room={ctx.room.name}"
    )
    
    # Subscribe to existing tracks
    subscribe_existing_tracks(ctx, event_handlers)
    
    # Register with orchestrator and get room_id
    room_id = await register_with_orchestrator(orchestrator, session_id)
    
    # Register cleanup callback
    cleanup = create_cleanup_callback(
        orchestrator, session_id, room_id,
        event_handlers, transcript_manager, tts_manager
    )
    ctx.add_shutdown_callback(cleanup)
    
    # Start SSE agent request listener
    await start_agent_request_listener(
        orchestrator, p.identity, session_id,
        control_state, event_handlers, tts_manager, ctx
    )
    
    # Setup DataChannel dispatcher for chat messages
    dispatcher = DataChannelDispatcher(orchestrator, room_id, session_id)
    ctx.room.on("data_received", dispatcher.create_dispatcher())
    logger.info("✅ DataChannel dispatcher registered")
    
    # Log readiness status
    if tts_manager:
        logger.info("🎤🔊 Vosk + TTS Agent ready and waiting for participants...")
    else:
        logger.info("🎤 Vosk Agent ready and waiting for participants...")
    
    # Keep agent alive forever (cleanup callback will be called on shutdown)
    await asyncio.Future()

MAX_ROOMS = 30
def load(worker) -> float:
    active_rooms = len(worker.active_jobs)
    # logger.info(f"Calculated load for Worker {worker.id} current active rooms: {active_rooms} load: {active_rooms / MAX_ROOMS}")
    return active_rooms / MAX_ROOMS

if __name__ == "__main__":

    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=config.livekit.agent_name,
        load_fnc=load,
        load_threshold=0.9
    ))
