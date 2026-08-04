"""
Agent Lifecycle Manager - Handles agent setup, cleanup, and integrations
"""
import logging
from typing import Optional

from livekit import agents
from src.core.tts_manager import TTSManager
from src.core.event_handlers import EventHandlers
from src.core.transcript_manager import TranscriptManager
from src.services.orchestrator_client import OrchestratorClient
from src.services.agent_request_handler import create_request_handlers


logger = logging.getLogger(__name__)


async def initialize_tts_manager(ctx: agents.JobContext, session_id: str) -> Optional[TTSManager]:
    """
    Initialize TTS Manager.
    
    Args:
        ctx: Agent JobContext
        session_id: Session ID
    
    Returns:
        TTSManager instance or None if initialization fails
    """
    try:
        logger.info("Initializing TTS Manager...")
        
        tts_manager = TTSManager(ctx=ctx, session_id=session_id)
        
        if await tts_manager.initialize():
            logger.info("✅ TTS Manager initialized successfully")
            return tts_manager
        else:
            logger.warning("⚠️ TTS Manager initialization failed, continuing without TTS")
            return None
            
    except Exception as e:
        logger.error(f"Failed to setup TTS Manager: {e}", exc_info=True)
        logger.warning("⚠️ Continuing without TTS functionality")
        return None


async def register_with_orchestrator(
    orchestrator: OrchestratorClient,
    session_id: str
) -> Optional[str]:
    """
    Register room with orchestrator and push session_started event.
    
    Args:
        orchestrator: OrchestratorClient instance
        session_id: Session ID (room name)
    
    Returns:
        room_id from orchestrator or None if registration fails
    """
    room_id = await orchestrator.register_room(session_id)
    if room_id:
        logger.info(f"✅ Room registered with orchestrator (room_id: {room_id})")
    else:
        logger.warning("⚠️ Failed to get room_id from orchestrator")
    
    return room_id


async def start_agent_request_listener(
    orchestrator: OrchestratorClient,
    agent_id: str,
    session_id: str,
    control_state,
    event_handlers,
    tts_manager,
    room_context
) -> None:
    """
    Start SSE agent request listener to receive requests from orchestrator.
    
    Args:
        orchestrator: OrchestratorClient instance
        agent_id: Agent identity
        session_id: Session ID (room name)
        control_state: AgentControlState instance
        event_handlers: EventHandlers instance
        tts_manager: TTSManager instance
        room_context: Room context (JobContext)
    """
    try:
        logger.info("Starting SSE agent request listener...")
        
        # Create and register request handlers with context
        handlers = create_request_handlers(
            control_state=control_state,
            event_handlers=event_handlers,
            tts_manager=tts_manager,
            room_context=room_context
        )
        
        for request_type, handler in handlers.items():
            orchestrator.register_request_handler(request_type, handler)
        
        # Start listener (subscribes to room-specific requests)
        await orchestrator.start_agent_request_listener(
            agent_id=agent_id,
            room_name=session_id
        )
        
        logger.info(
            f"✅ SSE agent request listener started "
            f"(agent_id={agent_id}, room={session_id})"
        )
    except Exception as e:
        logger.error(f"Failed to start SSE agent request listener: {e}", exc_info=True)
        logger.warning("⚠️ Continuing without SSE agent request listener")


def create_cleanup_callback(
    orchestrator: OrchestratorClient,
    session_id: str,
    event_handlers: EventHandlers,
    transcript_manager: TranscriptManager,
    tts_manager: Optional[TTSManager],
    room_id: Optional[str] = None,
):
    """
    Create cleanup callback for agent shutdown.

    Args:
        orchestrator: OrchestratorClient instance
        session_id: Session ID
        event_handlers: EventHandlers instance
        transcript_manager: TranscriptManager instance
        tts_manager: TTSManager instance (optional)
        room_id: This worker's own stable room UUID from registration
            (audio-ingestion PLAN.md D27), passed to unregister_room so
            orchestrator can compare-and-delete instead of blind-deleting
            by room_name -- protects against this call landing late, after
            room_name has already been reused by a new call.

    Returns:
        Async cleanup function
    """
    async def cleanup():
        """Cleanup when agent shuts down"""
        logger.info("🧹 Agent shutdown: cleaning resources...")

        # Unregister room and push session_ended event
        try:
            await orchestrator.unregister_room(session_id, room_id)
        except Exception as e:
            logger.error(f"unregister or session_ended event failed: {e}")
        
        # Stop SSE agent request listener
        try:
            logger.info("Stopping SSE agent request listener...")
            await orchestrator.stop_agent_request_listener()
            logger.info("✅ SSE agent request listener stopped")
        except Exception as e:
            logger.error(f"Failed to stop SSE listener: {e}")
        
        # Clear all request handlers to prevent memory leaks
        try:
            orchestrator.clear_all_handlers()
        except Exception as e:
            logger.error(f"Failed to clear request handlers: {e}")
        
        # Clean up event handlers and transcript manager
        await event_handlers.safe_disconnect_all()
        await transcript_manager.cleanup()
        
        # Clean up TTS manager
        if tts_manager:
            await tts_manager.cleanup()
        
        # Close orchestrator HTTP client
        try:
            await orchestrator.close()
        except Exception as e:
            logger.error(f"orchestrator close failed: {e}")
    
    return cleanup


def subscribe_existing_tracks(ctx: agents.JobContext, event_handlers: EventHandlers) -> None:
    """
    Subscribe to existing tracks in the room.
    
    Args:
        ctx: Agent JobContext
        event_handlers: EventHandlers instance
    """
    for participant in ctx.room.remote_participants.values():
        for pub in participant.track_publications.values():
            if not pub.subscribed:
                pub.set_subscribed(True)
                
                track = pub.track
                if track:
                    event_handlers.on_track_subscribed(track, pub, participant)
