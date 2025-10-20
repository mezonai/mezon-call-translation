from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from server_vosk.service.migration_controller import pipeline_controller
from server_vosk.session_manager import session_manager
from server_vosk.utils.websocket_monitor import websocket_monitor
from server_vosk.service.metrics_service import metrics
import asyncio
import time
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
    
    from ..service.result_dispatcher import get_result_dispatcher
    result_dispatcher = get_result_dispatcher()
    await result_dispatcher.register_client(session_id, client_id, websocket)
    
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
            # Track bytes received
            try:
                metrics.ws_bytes_received.labels(session_id=session_id).inc(len(data))
            except Exception:
                pass
            
            # Submit audio to client's individual pipeline
            try:
                # Optional chunk-id header support: b'CHID' + 8-byte little-endian chunk_id
                chunk_id = None
                payload = data
                if isinstance(data, (bytes, bytearray)) and len(data) >= 12 and data[:4] == b'CHID':
                    try:
                        chunk_id = int.from_bytes(data[4:12], 'little', signed=False)
                        payload = data[12:]
                        logger.debug("Parsed chunk_id=%s for client_id=%s session_id=%s", chunk_id, client_id, session_id)
                    except Exception:
                        # Malformed header, fall back to raw payload
                        payload = data
                        chunk_id = None
                # Forward payload and optional chunk_id to pipeline
                await pipeline_controller.submit_audio_async(payload, client_id, session_id, chunk_id=chunk_id)
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
        # Always ensure client is removed from all services
        session_manager.remove_client(session_id, client_id)
        
        # Unregister from result dispatcher
        try:
            from ..service.result_dispatcher import get_result_dispatcher
            result_dispatcher = get_result_dispatcher()
            await result_dispatcher.unregister_client(session_id, client_id)
        except Exception as e:
            logger.warning("Error unregistering from dispatcher: %s", e)
        
        # Cleanup client pipeline when disconnecting
        try:
            await pipeline_controller.cleanup_client(client_id, session_id)
            logger.info("Cleaned up client_id=%s from session_id=%s - pipeline destroyed", client_id, session_id)
        except Exception as e:
            logger.warning("Failed to cleanup client pipeline: %s", e)
            logger.info("Cleaned up client_id=%s from session_id=%s in session manager only", client_id, session_id)

@router.get("/ws/dispatcher-stats")
async def get_dispatcher_stats():
    """Get dispatcher performance statistics"""
    from ..service.result_dispatcher import get_result_dispatcher
    result_dispatcher = get_result_dispatcher()
    return {
        "dispatcher_stats": result_dispatcher.get_stats(),
        "timestamp": time.time(),
        "architecture": "per_client_concurrent_dispatchers"
    }

@router.get("/ws/stats")
async def get_websocket_stats():
    """Get WebSocket connection statistics for monitoring"""
    stats = websocket_monitor.get_stats()
    frequent_codes = websocket_monitor.get_frequent_disconnect_codes()
    
    return {
        "websocket_stats": stats,
        "frequent_disconnect_codes": frequent_codes,
        "active_clients_info": pipeline_controller.get_active_clients_info(),
        "circuit_breaker_status": pipeline_controller.get_circuit_breaker_status(),
        "service_info": pipeline_controller.get_service_info()
    }

@router.get("/ws/pipeline-distribution")
async def get_pipeline_distribution():
    """Get per-client pipeline distribution analysis"""
    return {
        "pipeline_distribution": pipeline_controller.get_pipeline_distribution(),
        "timestamp": time.time(),
        "recommendations": _get_pipeline_recommendations(pipeline_controller.get_pipeline_distribution())
    }

def _get_pipeline_recommendations(distribution_data: dict) -> list:
    """Generate recommendations based on pipeline distribution analysis"""
    recommendations = []
    
    if "error" in distribution_data:
        return ["Unable to analyze pipeline distribution due to error"]
    
    pipeline_dist = distribution_data.get("pipeline_distribution", {})
    total_pipelines = pipeline_dist.get("total_pipelines", 0)
    active_pipelines = pipeline_dist.get("active_pipelines", 0)
    max_clients = pipeline_dist.get("max_clients", 0)
    
    # Calculate utilization
    utilization = (total_pipelines / max_clients * 100) if max_clients > 0 else 0
    
    if utilization >= 90:
        recommendations.append(f"High utilization ({utilization:.1f}%). Consider increasing MAX_CONCURRENT_CLIENTS.")
    elif utilization >= 70:
        recommendations.append(f"Moderate utilization ({utilization:.1f}%). Monitor for capacity planning.")
    elif utilization < 20 and total_pipelines > 0:
        recommendations.append(f"Low utilization ({utilization:.1f}%). Consider reducing MAX_CONCURRENT_CLIENTS for resource optimization.")
    
    if active_pipelines < total_pipelines:
        idle_pipelines = total_pipelines - active_pipelines
        recommendations.append(f"{idle_pipelines} idle pipelines will be cleaned up automatically.")
    
    if total_pipelines == 0:
        recommendations.append("No active client pipelines. Per-client architecture is ready for incoming connections.")
    else:
        recommendations.append(f"Per-client pipeline architecture is working correctly with {total_pipelines} individual client pipelines.")
    
    return recommendations

@router.get("/ws/pipeline-health")
async def get_pipeline_health():
    """Get pipeline health information"""
    health = await pipeline_controller.get_pipeline_health()
    
    return {
        "pipeline_health": health,
        "timestamp": time.time(),
        "architecture": "per_client_pipelines"
    }

@router.get("/ws/service-info")
async def get_service_info():
    """Get service information and architecture details"""
    return {
        "service_info": pipeline_controller.get_service_info(),
        "timestamp": time.time()
    }

@router.post("/ws/pipeline/{client_id}/cleanup")
async def force_pipeline_cleanup(client_id: str, session_id: str = "default"):
    """Force cleanup of a specific client pipeline"""
    try:
        await pipeline_controller.cleanup_client(client_id, session_id)
        return {
            "success": True,
            "message": f"Pipeline cleanup completed for client {client_id}",
            "timestamp": time.time()
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "timestamp": time.time()
        }