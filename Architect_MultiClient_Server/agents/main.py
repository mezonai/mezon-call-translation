import asyncio
from livekit import agents

from src.core.transcript_manager import TranscriptManager
from src.core.handlers import EventHandlers
from src.logger import get_logger

logger = get_logger(__name__)


async def entrypoint(ctx: agents.JobContext):
    await ctx.connect()
    disconnected = asyncio.Event()

    # Initialize transcript manager for data channel communication
    transcript_manager = TranscriptManager(ctx)

    # Initialize event handlers
    event_handlers = EventHandlers(ctx, transcript_manager)

    async def on_disconnected():
        """Handle room disconnection"""
        logger.info("Room disconnected, cleaning up all clients")
        await event_handlers.safe_disconnect_all()
        disconnected.set()

    # Set up event handlers
    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

    # Set agent name
    await ctx.room.local_participant.set_name("Vosk Data Channel Transcription Agent")

    # Send welcome message via data channel
    await transcript_manager.send_welcome_message()

    logger.info("🎤 Vosk Data Channel Agent ready and waiting for participants...")
    
    try:
        await disconnected.wait()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("Shutting down agent...")
        await event_handlers.safe_disconnect_all()


if __name__ == "__main__":
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))