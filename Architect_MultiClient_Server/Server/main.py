import os
from fastapi import FastAPI
import asyncio
from contextlib import asynccontextmanager
from session_manager import session_manager

# Vosk STT engine
from controller.ws_vosk_control import router as stt_router
from service.vosk_service import stt_service_vosk as stt_service


async def result_dispatcher():
    """Fetch results from Vosk and send to clients."""
    while True:
        result = stt_service.get_result_nowait()
        if result:
            result_type, payload = result

            if result_type == "transcripts":
                clients = session_manager.get_clients_to_notify_transcript(payload["session_id"])
            elif result_type == "translation":
                clients = session_manager.get_clients_to_notify_translation(payload["session_id"])
            else:
                clients = []

            for ws in clients:
                await ws.send_json(payload)
        await asyncio.sleep(0.01)  # reduce CPU load


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → Shutdown lifecycle."""
    dispatcher_task = asyncio.create_task(result_dispatcher())
    yield
    stt_service.shutdown()
    dispatcher_task.cancel()
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass


# Init FastAPI
app = FastAPI(lifespan=lifespan)
app.include_router(stt_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8001,
        reload=False
    )
