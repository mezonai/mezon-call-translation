from fastapi import FastAPI
from controller.ws_vosk_control import router as vosk_router
from service.vosk_service import stt_service_vosk
from session_manager import session_manager
import asyncio
from contextlib import asynccontextmanager


async def result_dispatcher():
    """Vòng lặp lấy kết quả từ VOSK và gửi cho các client."""
    while True:
        vosk_result = stt_service_vosk.get_result_nowait()
        if vosk_result:
            text, client_id, session_id = vosk_result
            # Gửi text cho tất cả client trong cùng session
            for ws in session_manager.get_clients_to_notify(session_id):
                await ws.send_json({
                    "client_id": client_id,
                    "text": text
                })
        # Nghỉ một chút để giảm CPU load
        await asyncio.sleep(0.01)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời app: startup → shutdown."""
    # Startup
    dispatcher_task = asyncio.create_task(result_dispatcher())
    yield
    # Shutdown
    stt_service_vosk.shutdown()  # Dừng thread xử lý STT
    dispatcher_task.cancel()
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass


# Khởi tạo FastAPI với Lifespan
app = FastAPI(lifespan=lifespan)
app.include_router(vosk_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
