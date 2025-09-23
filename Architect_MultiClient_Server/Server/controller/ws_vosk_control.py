from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from ..service.vosk_service import stt_service_vosk
from ..session_manager import session_manager
from ..utils.websocket_monitor import websocket_monitor
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
    
    # Record connection for monitoring
    websocket_monitor.record_connection(client_id, session_id)

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
            except WebSocketDisconnect as disconnect_exc:
                # Handle WebSocket disconnect gracefully - this is normal behavior
                disconnect_code = getattr(disconnect_exc, 'code', 'unknown')
                disconnect_reason = getattr(disconnect_exc, 'reason', '')
                
                # Record disconnect for monitoring
                websocket_monitor.record_disconnect(client_id, session_id, disconnect_code, disconnect_reason)
                
                # Log based on disconnect code
                if disconnect_code == 1000:  # Normal closure
                    logger.info("WebSocket normal closure for client_id=%s session_id=%s (code=%s, reason='%s')", 
                               client_id, session_id, disconnect_code, disconnect_reason)
                elif disconnect_code == 1001:  # Going away
                    logger.info("WebSocket client going away for client_id=%s session_id=%s (code=%s, reason='%s')", 
                               client_id, session_id, disconnect_code, disconnect_reason)
                elif disconnect_code in [1006, 1011]:  # Abnormal closure or server error
                    logger.warning("WebSocket abnormal closure for client_id=%s session_id=%s (code=%s, reason='%s')", 
                                  client_id, session_id, disconnect_code, disconnect_reason)
                else:
                    logger.warning("WebSocket disconnect for client_id=%s session_id=%s (code=%s, reason='%s')", 
                                  client_id, session_id, disconnect_code, disconnect_reason)
                break  # Exit the receive loop
            except asyncio.TimeoutError:
                # Handle idle timeout
                logger.info("Idle timeout reached for client_id=%s session_id=%s (no data received for %ss)", 
                           client_id, session_id, idle_timeout)
                await websocket.close(code=1000, reason="Idle timeout")
                break
            except Exception as e:
                # Handle other unexpected errors during receive
                logger.error("Unexpected error during WebSocket receive for client_id=%s session_id=%s: %s", 
                           client_id, session_id, str(e), exc_info=True)
                break
            # Non-blocking submit with pre-VAD filtering to avoid event loop blocking
            try:
                await stt_service_vosk.submit_audio_async(data, client_id, session_id)
            except Exception as audio_error:
                logger.error("Error processing audio for client_id=%s session_id=%s: %s", 
                           client_id, session_id, str(audio_error), exc_info=True)
                # Continue processing other audio chunks - don't break connection for single audio error 
            # Enforce max duration
            if max_duration and max_duration > 0:
                now = asyncio.get_event_loop().time()
                if now - start_time >= max_duration:
                    logger.info("Max duration reached; closing client_id=%s session_id=%s", client_id, session_id)
                    await websocket.close(code=1000, reason="Max duration reached")
                    break
            # Enforce idle timeout (no audio for too long)
            if idle_timeout and idle_timeout > 0:
                now = asyncio.get_event_loop().time()
                if now - last_rx_time >= idle_timeout:
                    logger.info("Idle timeout reached; closing client_id=%s session_id=%s", client_id, session_id)
                    await websocket.close(code=1000, reason="Idle timeout")
                    break
    except Exception as e:
        logger.error("Unhandled error in websocket handler for client_id=%s session_id=%s: %s", 
                    client_id, session_id, str(e), exc_info=True)
    finally:
        # Always ensure client is removed from session manager on exit
        session_manager.remove_client(session_id, client_id)
        
        # Also cleanup from VoskService to keep metrics accurate
        try:
            stt_service_vosk.cleanup_client(client_id, session_id)
            logger.info("Cleaned up client_id=%s from session_id=%s in both session manager and vosk service", client_id, session_id)
        except Exception as e:
            logger.warning("Failed to cleanup client from VoskService: %s", e)
            logger.info("Cleaned up client_id=%s from session_id=%s in session manager only", client_id, session_id)

@router.get("/ws/stats")
async def get_websocket_stats():
    """Get WebSocket connection statistics for monitoring"""
    stats = websocket_monitor.get_stats()
    frequent_codes = websocket_monitor.get_frequent_disconnect_codes()
    
    return {
        "websocket_stats": stats,
        "frequent_disconnect_codes": frequent_codes,
        "active_clients_info": stt_service_vosk.get_active_clients_info(),
        "circuit_breaker_status": stt_service_vosk.get_circuit_breaker_status()
    }