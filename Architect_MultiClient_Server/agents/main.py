"""
LiveKit Agent entrypoint - Khởi động Vosk transcription agent
"""
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()
import uvicorn
from fastapi import FastAPI
import threading

from livekit import agents
from src.core.transcript_manager import TranscriptManager
from src.core.handlers import EventHandlers
from src.core.agent_manager import AgentManager
from src.logger import get_logger
from src.services.mongodb_service import get_mongodb_service
from src.api.dispatch_manager import router as dispatch_router
app = FastAPI()
app.include_router(dispatch_router, prefix="/api")
logger = get_logger(__name__)
agent_name = os.environ.get("LIVEKIT_AGENT_NAME")


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup và quản lý lifecycle"""
    await ctx.connect()
    disconnected = asyncio.Event()
    
    enable_mongodb = os.getenv('ENABLE_MONGODB', 'true').lower() == 'true'
    if enable_mongodb:
        mongodb = get_mongodb_service()
        await mongodb.connect()
        logger.info("✅ MongoDB initialized for transcript storage")


    transcript_manager = TranscriptManager(ctx)
    agent_manager = AgentManager(ctx)
    event_handlers = EventHandlers(ctx, transcript_manager, agent_manager)

    async def on_disconnected():
        """Cleanup khi room disconnect"""
        logger.info("Room disconnected, cleaning up all clients")
        await event_handlers.safe_disconnect_all()
        await agent_manager.cleanup()
        await transcript_manager.cleanup()
        disconnected.set()


    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

 
    def on_data_received(data):
        asyncio.create_task(agent_manager.handle_agent_commands(data))
    ctx.room.on("data_received", on_data_received)

    await agent_manager.setup_agent_identity()
    await agent_manager.announce_agent_ready()
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

def start_api():
    uvicorn.run("stt_agent:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":

    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()
    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=agent_name
    ))