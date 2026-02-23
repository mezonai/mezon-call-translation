# Import relative to Server package
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from tts_service.api.tts_api import router as tts_router
from tts_service.services.tts_engine import get_tts_engine
from tts_service.logger import get_logger
from contextlib import asynccontextmanager

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== STARTUP =====
    logger.info("FastAPI startup")
    engine = get_tts_engine()
    if not await engine.load():
        raise RuntimeError("Failed to load TTS engine")
    yield
    # ===== SHUTDOWN =====
    logger.info("FastAPI shutting down, cleaning up resources...")
    engine = get_tts_engine()
    engine.cleanup()
    logger.info("All services cleanup completed")

app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,  # Allow cookies to be included in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],     # Allow all headers
)

app.include_router(tts_router, prefix="/api")
