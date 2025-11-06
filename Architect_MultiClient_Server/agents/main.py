"""
LiveKit Agent entrypoint - Starts Vosk transcription agent + TTS
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
from src.core.event_handlers import EventHandlers
from src.core.agent_manager import AgentManager
from src.core.tts_manager import TTSManager
from src.logger import get_logger
from src.services.mongodb_service import get_mongodb_service
from src.api.dispatch_manager import router as dispatch_router
app = FastAPI()
app.include_router(dispatch_router, prefix="/api")
logger = get_logger(__name__)
agent_name = os.environ.get("LIVEKIT_AGENT_NAME")


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup and lifecycle management"""
    await ctx.connect()
    disconnected = asyncio.Event()
    logger.info(f"✅ Connected to room: {ctx.room.name}")
    
    # Get session_id from room name
    session_id = ctx.room.name
    
    enable_mongodb = os.getenv('ENABLE_MONGODB', 'true').lower() == 'true'
    if enable_mongodb:
        mongodb = get_mongodb_service()
        await mongodb.connect()
        logger.info("✅ MongoDB initialized for transcript storage")


    transcript_manager = TranscriptManager(ctx)
    agent_manager = AgentManager(ctx)
    event_handlers = EventHandlers(ctx, transcript_manager, agent_manager)

    # TTS Manager (optional, check if TTS is enabled)
    enable_tts = os.getenv('ENABLE_TTS', 'true').lower() == 'true'
    tts_manager = None
    
    if enable_tts:
        try:
            logger.info("Initializing TTS Manager...")
            
            # Get WebSocket URL from environment or use default
            ws_url = os.getenv('TTS_WS_URL', f"ws://localhost:8089/ws/tts/{session_id}")
            model_path = os.getenv('TTS_MODEL_PATH', 'models/silero_v3_en.pt')
            
            tts_manager = TTSManager(
                ctx=ctx,
                session_id=session_id,
                ws_url=ws_url,
                model_path=model_path
            )
            
            # Initialize TTS (load model, setup track, connect WebSocket)
            if await tts_manager.initialize():
                logger.info("✅ TTS Manager initialized successfully")
                # Announce TTS ready
                await tts_manager.announce_tts_ready()
            else:
                logger.warning("⚠️ TTS Manager initialization failed, continuing without TTS")
                tts_manager = None
                
        except Exception as e:
            logger.error(f"Failed to setup TTS Manager: {e}", exc_info=True)
            logger.warning("⚠️ Continuing without TTS functionality")
            tts_manager = None
    else:
        logger.info("TTS disabled (set ENABLE_TTS=true to enable)")


    async def on_disconnected():
        """Cleanup when room disconnects"""
        logger.info("Room disconnected, cleaning up all clients")
        await event_handlers.safe_disconnect_all()
        await agent_manager.cleanup()
        await transcript_manager.cleanup()
        
        # Cleanup TTS if enabled
        if tts_manager:
            await tts_manager.cleanup()
        
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

    # Log readiness status
    if tts_manager:
        logger.info("🎤🔊 Vosk + TTS Agent ready and waiting for participants...")
    else:
        logger.info("🎤 Vosk Agent ready and waiting for participants...")
    
    try:
        await disconnected.wait()
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        logger.info("Shutting down agent...")
        await event_handlers.safe_disconnect_all()
        await transcript_manager.cleanup()
        
        # Cleanup TTS if enabled
        if tts_manager:
            await tts_manager.cleanup()

def start_api():
    """Start FastAPI server for dispatch management (development/standalone mode only)"""
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)

if __name__ == "__main__":

    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()

    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        # agent_name=agent_name
    ))