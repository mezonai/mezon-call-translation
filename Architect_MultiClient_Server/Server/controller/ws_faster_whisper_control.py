from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from service.faster_whisper_service import stt_service, CHUNK_SIZE
from session_manager import session_manager
import asyncio

router = APIRouter()

@router.websocket("/ws/faster-whisper/")
async def websocket_faster_whisper(
    websocket: WebSocket,
    client_id: str = Query(...),
    session_id: str = Query(...),
    transcript: bool = Query(...),
    translation: bool = Query(...)
):
    await websocket.accept()
    session_manager.add_client(session_id, client_id, websocket, transcript, translation)
    
    # Buffer để tích lũy audio chunks
    buffer = bytearray()
    
    try:
        while True:
            data = await websocket.receive_bytes()
            buffer.extend(data)

            # Xử lý buffer theo chunk size
            while len(buffer) >= CHUNK_SIZE:
                chunk = buffer[:CHUNK_SIZE]
                buffer = buffer[CHUNK_SIZE:]
                
                stt_service.submit_audio(chunk, client_id, session_id)
    except WebSocketDisconnect:
        # if buffer:
        #    stt_service.submit_audio(buffer, client_id, session_id)
        session_manager.remove_client(session_id, client_id)

