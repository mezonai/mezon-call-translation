import asyncio
from dotenv import load_dotenv
load_dotenv()  # Load environment variables from .env file

from livekit import agents
from src.core.transcript_manager import TranscriptManager
from src.core.handlers import EventHandlers
from src.core.agent_manager import AgentManager
from src.logger import get_logger

logger = get_logger(__name__)


async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()

    # Initialize managers
    transcript_manager = TranscriptManager(ctx)
    agent_manager = AgentManager(ctx)
    
    # Initialize event handlers with agent manager
    event_handlers = EventHandlers(ctx, transcript_manager, agent_manager)

    async def on_disconnected():
        """Handle room disconnection"""
        logger.info("Room disconnected, cleaning up all clients")
        await event_handlers.safe_disconnect_all()
        await agent_manager.cleanup()  # Cleanup agent
        # THÊM: Cleanup Bot WebSocket
        await transcript_manager.cleanup()
        disconnected.set()

    # Set up event handlers
    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

    # Handle agent commands from data  
    def on_data_received(data):
        asyncio.create_task(agent_manager.handle_agent_commands(data))
    ctx.room.on("data_received", on_data_received)

    # Setup agent identity and announce ready status
    await agent_manager.setup_agent_identity()
    await agent_manager.announce_agent_ready()

    # Send welcome message via data channel
    await transcript_manager.send_welcome_message()

    logger.info("🎤 Vosk Agent ready and waiting for participants...")
    
    try:
        await disconnected.wait()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("Shutting down agent...")
        await event_handlers.safe_disconnect_all()
        await transcript_manager.cleanup()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint)) #,agent_name="Vosk-Transcription-Agent"