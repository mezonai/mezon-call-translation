import os
import logging
import time
from dotenv import load_dotenv
load_dotenv()

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.mongodb_service import get_mongodb_service
from orchestrator_service.config.application_config import get_config
from contextlib import asynccontextmanager

from orchestrator_service.api.dispatch_api import router as dispatch_router
from orchestrator_service.api.tts_api import router as tts_router
from orchestrator_service.api.stream_message_api import router as stream_router
from orchestrator_service.api.webhook_api import router as webhook_router, egress_service
from orchestrator_service.api.room_api import router as room_router
from orchestrator_service.api.track_api import router as track_router
from orchestrator_service.api.transcript_api import router as transcript_router
from orchestrator_service.services.livekit_client import cleanup_livekit_service

# Load config
config = get_config()
logger = get_logger(__name__)
from dotenv import load_dotenv


load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== STARTUP =====
    logger.info("FastAPI startup")
    if os.getenv("PHASE_2", "flase").upper() == "TRUE":             # Removed when moving to phase 2.  
        mongodb = get_mongodb_service()
        ok = await mongodb.connect()
        if not ok:
            raise RuntimeError("❌ MongoDB connection failed on startup")
        logger.info("✅ MongoDB connected on startup")
    else:                                                           # Removed when moving to phase 2.  
        logger.info("❌ MongoDB not connected on startup")          # Removed when moving to phase 2.
        mongodb = None                                              # Removed when moving to phase 2.
    yield
    # ===== SHUTDOWN =====
    logger.info("FastAPI shutting down, cleaning up resources...")
    await egress_service.cleanup()
    await cleanup_livekit_service()
    
    # Disconnect Mongo LAST
    if mongodb is not None:                                          # Removed when moving to phase 2.  
        await mongodb.disconnect()
    logger.info("All services cleanup completed")


# Cho phép frontend gọi API
origins = [
    "http://localhost:4200",   # Angular / React dev server
    "http://127.0.0.1:4200",
    "http://localhost:3000",   # React default
]

app = FastAPI(title="LiveKit Orchestrator API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(dispatch_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
app.include_router(webhook_router, prefix="/api/webhook", tags=["webhook"])
app.include_router(room_router)  # Has prefix="/api/transcripts/rooms"
app.include_router(track_router)  # Has prefix="/api/transcripts/tracks"
app.include_router(transcript_router)  # Has prefix="/api/transcripts"

