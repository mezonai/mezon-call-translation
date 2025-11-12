from fastapi import Request
from pydantic import BaseModel
from src.api.stream_message_manager import StreamMessageManager
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from src.api.verify_account import authenticate_account
import os
import asyncio
router = APIRouter()
manager = StreamMessageManager()

class PushMessageRequest(BaseModel):
    room_name: str
    message: str

@router.post("/push_message")
async def push_message_api(req: PushMessageRequest):
    queue = manager.get_queue(req.room_name)
    queue.put(req.message)
    return {"status": "ok", "room": req.room_name, "message": req.message}




async def event_generator(room_name: str):
    
    print(f"[DEBUG-Thread-B] event_generator called for room: {room_name}")
    print(f"[DEBUG-Thread-B] PID: {os.getpid()}, StreamMessageManager id: {id(manager)}")
    queue = manager.get_queue(room_name)
    print(f"[DEBUG-Thread-B] Current queues: {list(manager.queues.keys())}")
    print(f"[DEBUG-Thread-B] Queues object ids: {[id(q) for q in manager.queues.values()]}")
    loop = asyncio.get_event_loop()
    while True:
        text = await loop.run_in_executor(None, queue.get)
        print(f"[SSE] Popped text from queue (room={room_name}): {text}")
        yield f"data: {text}\n\n"



@router.get("/stream_message")
async def sse_endpoint( appid: str, token: str, room: str):
    account = {"appid": appid, "token": token}

    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")

    return StreamingResponse(
        event_generator( room),
        media_type="text/event-stream"
    )
