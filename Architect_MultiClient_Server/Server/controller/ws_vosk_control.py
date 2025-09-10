from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from ..service.vosk_service import stt_service_vosk
from ..session_manager import session_manager
import asyncio
from typing import Optional
import logging

logger = logging.getLogger(__name__)

router = APIRouter()

@router.websocket("/ws/vosk/")
async def websocket_vosk(
    websocket: WebSocket,
    client_id: str = Query(...),
    session_id: str = Query(...),
    transcript: bool = Query(...),
    translation: bool = Query(...),
    language: Optional[str] = Query(default=None),
    max_duration: Optional[int] = Query(default=None, description="Max session duration in seconds"),
    idle_timeout: Optional[int] = Query(default=None, description="Disconnect if no audio received for N seconds")
):
    await websocket.accept()
    logger.info(
        "WebSocket accepted for client_id=%s session_id=%s transcript=%s translation=%s language=%s max_duration=%s idle_timeout=%s",
        client_id,
        session_id,
        transcript,
        translation,
        language,
        max_duration,
        idle_timeout,
    )
    logger.info("Registering client with session manager")
    session_manager.add_client(session_id, client_id, websocket, transcript, translation, language)

    # Ensure client is properly registered for targeted communication
    logger.info("Client registered: session_id=%s, client_id=%s", session_id, client_id)
    try:
        start_time = asyncio.get_event_loop().time()
        last_rx_time = start_time
        while True:
            try:
                if idle_timeout and idle_timeout > 0:
                    data = await asyncio.wait_for(websocket.receive_bytes(), timeout=idle_timeout)
                else:
                    data = await websocket.receive_bytes()
                last_rx_time = asyncio.get_event_loop().time()
            except Exception:
                logger.exception("WebSocket receive error for client_id=%s session_id=%s", client_id, session_id)
                raise
            # Non-blocking submit with pre-VAD filtering to avoid event loop blocking
            await stt_service_vosk.submit_audio_async(data, client_id, session_id)
            pass 
            # Enforce max duration
            if max_duration and max_duration > 0:
                now = asyncio.get_event_loop().time()
                if now - start_time >= max_duration:
                    logger.info("Max duration reached; closing client_id=%s session_id=%s", client_id, session_id)
                    await websocket.close(code=1000)
                    break
            # Enforce idle timeout (no audio for too long)
            if idle_timeout and idle_timeout > 0:
                now = asyncio.get_event_loop().time()
                if now - last_rx_time >= idle_timeout:
                    logger.info("Idle timeout reached; closing client_id=%s session_id=%s", client_id, session_id)
                    await websocket.close(code=1000)
                    break
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for client_id=%s session_id=%s", client_id, session_id)
    except Exception:
        logger.exception("Unhandled error in websocket handler for client_id=%s session_id=%s", client_id, session_id)
    finally:
        # Always ensure client is removed from session manager on exit
        session_manager.remove_client(session_id, client_id)
        logger.info("Cleaned up client_id=%s from session_id=%s", client_id, session_id)