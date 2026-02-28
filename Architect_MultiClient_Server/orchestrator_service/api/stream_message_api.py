from fastapi import Request
import json
from pydantic import BaseModel
from typing import Optional
from orchestrator_service.api.stream_message_manager import StreamMessageManager
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from orchestrator_service.auth.verify_account import authenticate_account
import asyncio
from orchestrator_service.utils.logger import get_logger
logger = get_logger(__name__)
router = APIRouter()
manager = StreamMessageManager()

class PushMessageRequest(BaseModel):
    room_name: str
    message: str
    message_type: str
    participant_identity: Optional[str] = None

@router.post("/push_message")
async def push_message_api(req: PushMessageRequest):
    # check room has active connections
    if not manager.has_active_connections(req.room_name):
        logger.warning(f"No active connections for room {req.room_name}, message may be lost")
    
    # Broadcast message to all subscribers in the room (Pub/Sub pattern)
    message_data = {
        "message": req.message, 
        "type": req.message_type,
        "participant_identity": req.participant_identity
    }
    
    broadcast_count = await manager.broadcast_message(req.room_name, message_data)
    
    return {
        "status": "ok", 
        "room": req.room_name, 
        "message": req.message,
        "message_type": req.message_type,
        "active_connections": manager.get_connection_count(req.room_name),
        "broadcast_to": broadcast_count  # Number of connections that received the message
    }


async def event_generator(room_name: str, connection_id: str, connection_queue: asyncio.Queue):
    """
    Generator SSE events for a specific connection.
    Each connection has its own queue (Pub/Sub pattern).
    
    Features:
    - Dedicated queue per connection - all messages broadcast to all subscribers
    - Near-instant delivery: messages sent immediately when available
    - Timeout to detect client disconnect
    - Proper cleanup when connection closes
    - Heartbeat to keep connection alive
    """
    logger.info(f"[SSE] Connection started: {connection_id} for room: {room_name}")
    
    # Send initial event to confirm connection
    yield f"event: connected\ndata: {connection_id}\n\n"
    
    heartbeat_interval = 15  # Send heartbeat every 15 seconds
    last_heartbeat = asyncio.get_event_loop().time()
    
    try:
        while True:
            try:
                # OPTIMIZED: Short timeout for near-instant message delivery
                # Messages are sent within 100ms of being queued
                data = await asyncio.wait_for(
                    connection_queue.get(),
                    timeout=0.1  # 100ms - balance between responsiveness and CPU usage
                )
                logger.info(f"[SSE] Sending to {connection_id}: {data['type']} {data['message'][:50] if len(data['message']) > 50 else data['message']}...")
                yield f"data: {json.dumps(data)}\n\n"
                
            except asyncio.TimeoutError:
                # No new message - check if heartbeat needs to be sent
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield f"event: heartbeat\ndata: ping\n\n"
                    last_heartbeat = current_time
                continue
                
            except asyncio.CancelledError:
                # Client disconnect
                logger.info(f"[SSE] Connection cancelled: {connection_id}")
                break
                
    except GeneratorExit:
        logger.info(f"[SSE] Generator exit: {connection_id}")
    finally:
        # Cleanup when connection closes
        manager.unregister_connection(room_name, connection_id)
        logger.info(f"[SSE] Connection closed and unregistered: {connection_id}")


@router.get("/stream_message")
async def sse_endpoint(appid: str, token: str, room: str, client_id: Optional[str] = None):
    """
    SSE endpoint for real-time message streaming.
    
    Args:
        appid: Application ID for authentication
        token: Authentication token
        room: Room name to subscribe to
        client_id: Optional client identifier to prevent duplicate connections
                   If provided, will close existing connection from same client
    
    Returns:
        StreamingResponse with SSE events
    """
    account = {"appid": appid, "token": token}

    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")

    # If client_id provided, close existing connection from same client
    if client_id:
        existing_disconnected = manager.disconnect_existing_client(room, client_id)
        if existing_disconnected:
            logger.info(f"[SSE] Closed existing connection for client {client_id} in room {room}")

    # Register new connection and create dedicated queue
    connection_id = manager.register_connection(room, client_id)
    connection_queue = manager.create_connection_queue(room, connection_id)
    
    logger.info(
        f"[SSE] New connection registered: {connection_id} (client={client_id}), "
        f"total for room {room}: {manager.get_connection_count(room)}"
    )

    return StreamingResponse(
        event_generator(room, connection_id, connection_queue),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
