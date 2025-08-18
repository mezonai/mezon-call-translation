from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from service.vosk_service import stt_service_vosk
from session_manager import session_manager
import asyncio
from typing import Optional

router = APIRouter()

@router.websocket("/ws/vosk/")
async def websocket_vosk(
    websocket: WebSocket,
    client_id: str = Query(...),
    session_id: str = Query(...),
    transcript: bool = Query(...),
    translation: bool = Query(...),
    language: Optional[str] = Query(default=None)
):
    await websocket.accept()
    session_manager.add_client(session_id, client_id, websocket, transcript, translation, language)
    try:
        while True:
            data = await websocket.receive_bytes()
            stt_service_vosk.submit_audio(data, client_id, session_id)
            pass 
    except WebSocketDisconnect:
        session_manager.remove_client(session_id, client_id)