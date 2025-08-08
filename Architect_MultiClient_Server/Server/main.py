from fastapi import FastAPI
from controller.ws_faster_whisper_control import router as faster_whisper_router
from service.faster_whisper_service import stt_service
from session_manager import session_manager
import asyncio

app = FastAPI()
app.include_router(faster_whisper_router)

async def result_dispatcher():
    while True:
        result = stt_service.get_result_nowait()
        if result:
            text, client_id, session_id = result
            # session_manager.update_transcript(session_id, client_id, text)
            # response = session_manager.get_transcript_json(session_id)
            # for ws in session_manager.get_clients_to_notify(session_id):
            #     await ws.send_json(response)
            for ws in session_manager.get_clients_to_notify(session_id):
                response = {
                    # "session_id": session_id,
                    "client_id": client_id,
                    "text": text
                }
            await ws.send_json(response)
        else:
            await asyncio.sleep(0.01)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(result_dispatcher())

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="172.16.220.225",
        port=8000,
        reload=True
    )

