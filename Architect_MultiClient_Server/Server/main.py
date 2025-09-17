import os
import time
import logging
from fastapi import FastAPI
import asyncio
from contextlib import asynccontextmanager
from .session_manager import session_manager

#agent service
from .controller.agents_control import router as agents_control

# Vosk STT engine
from .controller.ws_vosk_control import router as stt_router
from .service.vosk_service import stt_service_vosk as stt_service
from .service.health_service import get_health_service
from .utils.logging_config import setup_logging
from dotenv import load_dotenv

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware



# Load environment variables
load_dotenv()

async def result_dispatcher(async_result_queue: asyncio.Queue):
    """Fetch results from Vosk (async queue) and send to clients without polling."""
    while True:
        try:
            result_type, payload = await async_result_queue.get()
            logger.debug("Dispatcher received: type=%s, payload=%s", result_type, payload)

            if result_type == "transcripts":
                # Pass sender_client_id to only send to the client who generated the transcript
                sender_client_id = payload.get("client_id")
                clients = session_manager.get_clients_to_notify_transcript(
                    payload["session_id"], 
                    sender_client_id=sender_client_id
                )
                logger.debug("Found %s transcript clients for session %s (sender: %s)", 
                           len(clients), payload["session_id"], sender_client_id)
                
            elif result_type == "translation":
                # Pass sender_client_id to only send to the client who generated the translation
                sender_client_id = payload.get("client_id")
                clients = session_manager.get_clients_to_notify_translation(
                    payload["session_id"],
                    sender_client_id=sender_client_id
                )
                logger.debug("Found %s translation clients for session %s (sender: %s)", 
                           len(clients), payload["session_id"], sender_client_id)
            else:
                clients = []

            for ws in clients:
                try:
                    await ws.send_json(payload)
                    logger.debug("Sent result to sender client: %s", payload)
                except Exception as e:
                    logger.warning("Failed to send to sender client (session_id=%s, client_id=%s). Client likely disconnected: %s", 
                                 payload.get("session_id"), payload.get("client_id"), e)
        except Exception:
            logger.exception("Dispatcher loop error; continuing")

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → Shutdown lifecycle."""
    async_result_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
    # Provide loop and queue to service for thread-safe result emission
    stt_service.set_async_result_queue(asyncio.get_event_loop(), async_result_queue)
    dispatcher_task = asyncio.create_task(result_dispatcher(async_result_queue))
    yield
    stt_service.shutdown()
    dispatcher_task.cancel()
    try:
        await dispatcher_task
    except asyncio.CancelledError:
        pass


# Init logging and FastAPI
# Get log level from environment variable
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level)
logger = logging.getLogger(__name__)

app = FastAPI(lifespan=lifespan)
# Cho phép frontend gọi API
origins = [
    "http://localhost:4200",   # Angular / React dev server
    "http://127.0.0.1:4200",   # nếu chạy khác host
    "http://localhost:3000",   # nếu dùng React default
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,          # domain được phép
    allow_credentials=True,
    allow_methods=["*"],            # GET, POST, PUT, DELETE...
    allow_headers=["*"],            # Cho phép tất cả headers
)



app.include_router(stt_router)
app.include_router(agents_control)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    health_service = get_health_service()
    health_status = health_service.get_health_status()
    
    status_code = 200
    if health_status.status == "unhealthy":
        status_code = 503
    elif health_status.status == "degraded":
        status_code = 200  # Still operational but degraded
    
    return {
        "status": health_status.status,
        "timestamp": health_status.timestamp,
        "uptime": health_status.uptime,
        "details": health_status.details
    }


@app.get("/health/simple")
async def simple_health_check():
    """Simple health check endpoint."""
    health_service = get_health_service()
    is_healthy = health_service.is_healthy()
    
    return {
        "status": "healthy" if is_healthy else "unhealthy",
        "timestamp": time.time()
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )