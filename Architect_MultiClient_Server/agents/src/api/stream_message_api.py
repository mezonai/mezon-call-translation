from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from src.api.verify_account import authenticate_account
from src.api.stream_message_manager import stream_message_manager

router = APIRouter()

async def event_generator(room_name: str):
    queue = await stream_message_manager.get_queue(room_name)
    while True:
        text = await queue.get()
        print(f"[SSE] Popped text from queue (room={room_name}): {text}")
        yield f"data: {text}\n\n"


@router.get("/stream_message")
async def sse_endpoint(appid: str, token: str, room: str):
    account = {"appid": appid, "token": token}

    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")

    return StreamingResponse(
        event_generator(room),
        media_type="text/event-stream"
    )
