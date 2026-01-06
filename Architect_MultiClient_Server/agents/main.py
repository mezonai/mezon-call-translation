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
# from src.core.agent_manager import AgentManager
from src.core.tts_manager import TTSManager
from src.logger import get_logger
from src.services.mongodb_service import get_mongodb_service


from src.api.dispatch_api import router as dispatch_router
from src.api.tts_api import router as tts_router
from src.api.stream_message_api import router as stream_router
app = FastAPI()
app.include_router(dispatch_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
logger = get_logger(__name__)
agent_name = os.getenv("LIVEKIT_AGENT_NAME")


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup and lifecycle management"""
    from livekit import api

    # tạo token mới tạo để đổi được identity để hiển thị trong room
    new_token = api.AccessToken(os.getenv("LIVEKIT_API_KEY"), os.getenv("LIVEKIT_API_SECRET"))
    new_token.with_identity("KOMU")
    new_token.with_name("KOMU Agent")
    new_token.with_grants(api.VideoGrants(
        room_join=True,
        room=ctx.room.name,
        can_publish=True,
        can_subscribe=True
    ))

    # ghi đè token trong ctx
    ctx._info.token = new_token.to_jwt()
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
    # agent_manager = AgentManager(ctx)
    event_handlers = EventHandlers(ctx, transcript_manager)#, agent_manager

    # TTS Manager (optional, check if TTS is enabled)
    enable_tts = os.getenv('ENABLE_TTS', 'true').lower() == 'true'
    tts_manager = None
    
    if enable_tts:
        try:
            logger.info("Initializing TTS Manager...")
            
            # Get model path from environment (Kokoro model directory)
            model_path = os.getenv('TTS_MODEL_PATH', 'models/kokoro_models')
            
            tts_manager = TTSManager(
                ctx=ctx,
                session_id=session_id,
                model_path=model_path
            )
            
            # Initialize TTS (load model, setup track)
            if await tts_manager.initialize():
                logger.info("✅ TTS Manager initialized successfully")
                
                # Note: DataChannel routing is handled by central dispatcher in main.py
                logger.info("✅ TTS listening on DataChannel topic='tts_control'")
                
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
        # await agent_manager.cleanup()
        await transcript_manager.cleanup()
        
        # Cleanup TTS if enabled
        if tts_manager:
            await tts_manager.cleanup()
        
        disconnected.set()


    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

 
    def on_data_received(data_packet):
        """Central DataChannel dispatcher - routes messages to appropriate handlers"""
        try:
            topic = data_packet.topic
            participant_id = data_packet.participant.identity if data_packet.participant else "unknown"
            
            # Log incoming data for debugging
            logger.info(f"📩 DataChannel received: topic='{topic}' from {participant_id}")
            
            # Route to appropriate handler based on topic
            # if topic == "agent_commands":
            #     logger.debug(f"→ Routing to AgentManager")
            #     asyncio.create_task(agent_manager.handle_agent_commands(data_packet))
            if topic == "tts_control":
                if tts_manager:
                    logger.info(f"🎯 Routing to TTSManager")
                    asyncio.create_task(tts_manager.handle_tts_data(data_packet))
                else:
                    logger.warning("⚠️ Received TTS request but TTS is not enabled")
            else:
                logger.debug(f"Unhandled DataChannel topic: {topic}")
                
        except Exception as e:
            logger.error(f"❌ Error in DataChannel dispatcher: {e}", exc_info=True)
    
    ctx.room.on("data_received", on_data_received)

    # await agent_manager.setup_agent_identity()
    # await agent_manager.announce_agent_ready()
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

    uvicorn.run("main:app", host=os.getenv("AGENT_HOST", "0.0.0.0"), port=int(os.getenv("AGENT_PORT", "8002")), reload=False)

if __name__ == "__main__":

    api_thread = threading.Thread(target=start_api, daemon=True)
    api_thread.start()

    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=agent_name
    ))
