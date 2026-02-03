"""
LiveKit Agent entrypoint - Starts Vosk transcription agent + TTS
"""
import asyncio
from dotenv import load_dotenv
load_dotenv()

from livekit import agents
from src.core.transcript_manager import TranscriptManager
from src.core.event_handlers import EventHandlers
from src.core.tts_manager import TTSManager
from src.logger import get_logger
from src.config.application_config import get_config


# Load config
config = get_config()
logger = get_logger(__name__)


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup and lifecycle management"""
    from livekit import api

    # Create a new token to change the identity displayed in the room
    new_token = api.AccessToken(config.livekit.api_key, config.livekit.api_secret)
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

    transcript_manager = TranscriptManager(ctx)
    event_handlers = EventHandlers(ctx, transcript_manager)

    
    for participant in ctx.room.remote_participants.values():
        for pub in participant.track_publications.values():

            if not pub.subscribed:

                pub.set_subscribed(True)

                track = pub.track
                if track:
                    # ✅ reuse handler
                    event_handlers.on_track_subscribed(track, pub, participant)

    # TTS Manager (optional, check if TTS is enabled)
    tts_manager = None
    
    if config.tts.enabled:
        try:
            logger.info("Initializing TTS Manager...")
            
            # Get model path from config
            tts_manager = TTSManager(
                ctx=ctx,
                session_id=session_id,
                model_path=config.tts.model_path
            )
            
            def on_data_received(data_packet):
                """Central DataChannel dispatcher - routes messages to appropriate handlers"""
                try:
                    topic = data_packet.topic
                    participant_id = data_packet.participant.identity if data_packet.participant else "unknown"
                    
                    # Log incoming data for debugging
                    logger.info(f"📩 DataChannel received: topic='{topic}' from {participant_id}")
                    
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



    ctx.room.on("disconnected", lambda: asyncio.create_task(on_disconnected()))

 


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

if __name__ == "__main__":

    agents.cli.run_app(agents.WorkerOptions(
        entrypoint_fnc=entrypoint,
        agent_name=config.livekit.agent_name
    ))
