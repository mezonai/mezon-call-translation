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
from src.core.agent_control_state import AgentControlState
from src.logger import get_logger
from src.config.application_config import get_config
from src.services import orchestrator_client
from livekit import api

# Load config
config = get_config()
logger = get_logger(__name__)


async def entrypoint(ctx: agents.JobContext):
    """Main agent entrypoint - setup and lifecycle management"""

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

    # ghi đè token trong ctx
    ctx._info.token = new_token.to_jwt()
    

    logger.info(f"✅ Connected to room: {ctx.room.name}")
    # Get session_id from room name
    session_id = ctx.room.name

    transcript_manager = TranscriptManager(ctx)
    control_state = AgentControlState(transcription_enabled=False)
    event_handlers = EventHandlers(ctx, transcript_manager, control_state=control_state)

    ctx.room.on("track_subscribed", event_handlers.on_track_subscribed)
    ctx.room.on("track_unsubscribed", event_handlers.on_track_unsubscribed)
    ctx.room.on("participant_disconnected", event_handlers.on_participant_disconnected)
        
    async def cleanup():
        """Cleanup when agent shuts down"""
        logger.info("🧹 Agent shutdown: cleaning resources...")

        try:
            await orchestrator_client.unregister_room(session_id)
        except Exception as e:
            logger.error(f"unregister failed: {e}")

        await event_handlers.safe_disconnect_all()
        await transcript_manager.cleanup()

        if tts_manager:
            await tts_manager.cleanup()

    # Register cleanup callback
    ctx.add_shutdown_callback(lambda: asyncio.create_task(cleanup()))

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

    def _parse_json_bytes(data: bytes):
        import json
        try:
            if data is None:
                return None
            if isinstance(data, (bytes, bytearray)):
                s = bytes(data).decode("utf-8", errors="strict")
            else:
                s = str(data)
            return json.loads(s)
        except Exception:
            return None

    async def _send_agent_control_ack(status: str, details: dict | None = None):
        import json, time
        msg = {
            "type": "agent_control_ack",
            "status": status,
            "timestamp": int(time.time() * 1000),
            "details": details or {},
        }
        try:
            await ctx.room.local_participant.publish_data(
                json.dumps(msg).encode("utf-8"),
                reliable=True,
                topic="agent_control_ack",
            )
        except Exception as e:
            logger.debug(f"Failed to send agent_control_ack: {e}")

    async def _handle_agent_control(data_packet):
        payload = _parse_json_bytes(getattr(data_packet, "data", None))
        sender = data_packet.participant.identity if data_packet.participant else "unknown"
        if not payload:
            logger.warning(f"agent_control: invalid payload from {sender}")
            await _send_agent_control_ack("error", {"error": "invalid_payload"})
            return

        action = payload.get("action") or payload.get("type")
        if not action:
            await _send_agent_control_ack("error", {"error": "missing_action"})
            return

        action = str(action).lower()
        if action in ("start_transcription", "start", "enable_transcription", "enable"):
            changed = await control_state.set_transcription_enabled(True)
            started = await event_handlers.start_transcription_for_all_pending()
            logger.info(
                f"agent_control: transcription_enabled=True by {sender} (changed={changed}, started={started})"
            )
            await _send_agent_control_ack("ok", {"transcription_enabled": True, "started": started, "changed": changed})
        elif action in ("stop_transcription", "stop", "disable_transcription", "disable"):
            changed = await control_state.set_transcription_enabled(False)
            stopped = await event_handlers.stop_transcription_for_all()
            logger.info(
                f"agent_control: transcription_enabled=False by {sender} (changed={changed}, stopped={stopped})"
            )
            await _send_agent_control_ack("ok", {"transcription_enabled": False, "stopped": stopped, "changed": changed})
        elif action in ("status", "get_status"):
            enabled = await control_state.get_transcription_enabled()
            await _send_agent_control_ack("ok", {"transcription_enabled": enabled})
        else:
            await _send_agent_control_ack("error", {"error": "unknown_action", "action": action})

    def on_data_received(data_packet):
        """Central DataChannel dispatcher - routes messages to appropriate handlers"""
        try:
            topic = data_packet.topic
            print(f"Received data packet: {data_packet}")
            participant_id = data_packet.participant.identity if data_packet.participant else "unknown"
            logger.info(f"📩 DataChannel received: topic='{topic}' from {participant_id}")
            print(data_packet)
            if topic == "tts_control":
                if tts_manager:
                    logger.info("🎯 Routing to TTSManager")
                    asyncio.create_task(tts_manager.handle_tts_data(data_packet))
                else:
                    logger.warning("⚠️ Received TTS request but TTS is not enabled")
            elif topic == "agent_control":
                asyncio.create_task(_handle_agent_control(data_packet))
            else:
                logger.debug(f"Unhandled DataChannel topic: {topic}")
        except Exception as e:
            logger.error(f"❌ Error in DataChannel dispatcher: {e}", exc_info=True)

    # Always register dispatcher (even if TTS is disabled)
    ctx.room.on("data_received", on_data_received)





    try:
        logger.info("Initializing TTS Manager...")
        
        # Get model path from config
        tts_manager = TTSManager(
            ctx=ctx,
            session_id=session_id
        )

        # Initialize TTS (load model, setup track)
        if await tts_manager.initialize():
            logger.info("✅ TTS Manager initialized successfully")
            
            # Note: DataChannel routing is handled by central dispatcher in main.py
            logger.info("✅ TTS listening on DataChannel topic='tts_control'")
            
        else:
            logger.warning("⚠️ TTS Manager initialization failed, continuing without TTS")
            tts_manager = None
            
    except Exception as e:
        logger.error(f"Failed to setup TTS Manager: {e}", exc_info=True)
        logger.warning("⚠️ Continuing without TTS functionality")
        tts_manager = None


    await ctx.connect()
    p = ctx.room.local_participant

    logger.info(
        f"[AGENT STARTED] "
        f"identity={p.identity} | "
        f"name={p.name} | "
        f"sid={p.sid} | "
        f"room={ctx.room.name}"
    )
    # Register room with orchestrator for webhook processing
    await orchestrator_client.register_room(session_id)
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
