from fastapi import FastAPI
from controller.ws_vosk_control import router as vosk_router
# from controller.ws_faster_whisper_control import router as whisper_router
from service.vosk_service import stt_service_vosk
# from service.faster_whisper_service import stt_service
from session_manager import session_manager
import asyncio
from contextlib import asynccontextmanager


async def result_dispatcher():
    """Vòng lặp lấy kết quả từ VOSK và gửi cho các client."""
    while True:
        vosk_result = stt_service_vosk.get_result_nowait()
        if vosk_result:
            result_type, payload = vosk_result

            if result_type == "transcripts":
                clients = session_manager.get_clients_to_notify_transcript(payload["session_id"])
            elif result_type == "translation":
                clients = session_manager.get_clients_to_notify_translation(payload["session_id"])
            else:
                clients = []

            for ws in clients:
                await ws.send_json(payload)
        # Nghỉ một chút để giảm CPU load
        await asyncio.sleep(0.01)

# async def result_dispatcher():
#     """Vòng lặp lấy kết quả từ VOSK và gửi cho các client."""
#     while True:
#         whiaper_result = stt_service.get_result_nowait()
#         if whiaper_result:
#             result_type, payload = whiaper_result

#             if result_type == "transcripts":
#                 clients = session_manager.get_clients_to_notify_transcript(payload["session_id"])
#             elif result_type == "translation":
#                 clients = session_manager.get_clients_to_notify_translation(payload["session_id"])
#             else:
#                 clients = []

#             for ws in clients:
#                 await ws.send_json(payload)
#         # Nghỉ một chút để giảm CPU load
#         await asyncio.sleep(0.01)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Quản lý vòng đời app: startup → shutdown."""
    # Startup
    dispatcher_task = asyncio.create_task(result_dispatcher())
    yield
    # Shutdown
    stt_service_vosk.shutdown()  
    # stt_service.shutdown()
    dispatcher_task.cancel()
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass


# Khởi tạo FastAPI với Lifespan
app = FastAPI(lifespan=lifespan)
app.include_router(vosk_router)
# app.include_router(whisper_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )
