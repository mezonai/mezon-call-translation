from fastapi import Request
from pydantic import BaseModel
from src.api.stream_message_manager import StreamMessageManager
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from src.api.verify_account import authenticate_account
import os
import asyncio
import queue
from src.logger import get_logger
logger = get_logger(__name__)
router = APIRouter()
manager = StreamMessageManager()

class PushMessageRequest(BaseModel):
    room_name: str
    message: str

@router.post("/push_message")
async def push_message_api(req: PushMessageRequest):
    # Kiểm tra có client nào đang listen không
    if not manager.has_active_connections(req.room_name):
        logger.warning(f"No active connections for room {req.room_name}, message may be lost")
    
    q = manager.get_queue(req.room_name)
    q.put(req.message)
    return {
        "status": "ok", 
        "room": req.room_name, 
        "message": req.message,
        "active_connections": manager.get_connection_count(req.room_name)
    }


async def event_generator(room_name: str, connection_id: str):
    """
    Generator SSE với:
    - Timeout để detect client disconnect
    - Proper cleanup khi connection đóng
    - Heartbeat để giữ connection alive
    """
    logger.info(f"[SSE] Connection started: {connection_id} for room: {room_name}")
    q = manager.get_queue(room_name)
    loop = asyncio.get_event_loop()
    
    # Gửi event đầu tiên để confirm connection
    yield f"event: connected\ndata: {connection_id}\n\n"
    
    heartbeat_interval = 15  # Gửi heartbeat mỗi 15 giây
    last_heartbeat = asyncio.get_event_loop().time()
    
    try:
        while True:
            try:
                # Sử dụng timeout để có thể check connection và gửi heartbeat
                text = await asyncio.wait_for(
                    loop.run_in_executor(None, lambda: q.get(timeout=1.0)),
                    timeout=2.0
                )
                logger.info(f"[SSE] Sending to {connection_id}: {text[:50]}...")
                yield f"data: {text}\n\n"
                
            except (asyncio.TimeoutError, queue.Empty):
                # Không có message mới - kiểm tra có cần gửi heartbeat không
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
        # Cleanup khi connection đóng
        manager.unregister_connection(room_name, connection_id)
        logger.info(f"[SSE] Connection closed and unregistered: {connection_id}")


@router.get("/stream_message")
async def sse_endpoint(appid: str, token: str, room: str):
    account = {"appid": appid, "token": token}

    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")

    # Đăng ký connection mới
    connection_id = manager.register_connection(room)
    logger.info(f"[SSE] New connection registered: {connection_id}, total for room {room}: {manager.get_connection_count(room)}")

    return StreamingResponse(
        event_generator(room, connection_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )
