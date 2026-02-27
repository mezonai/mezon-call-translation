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
from orchestrator_service.api.chat_external_api import router as chat_external_router
from orchestrator_service.api.stream_message_api import router as stream_router
from orchestrator_service.api.webhook_api import router as webhook_router, egress_service
from orchestrator_service.api.room_api import router as room_router
from orchestrator_service.api.track_api import router as track_router
from orchestrator_service.api.transcript_api import router as transcript_router
from orchestrator_service.api.agent_control_api import router as agent_control_router
from orchestrator_service.api.room_registry_api import router as room_registry_router
from orchestrator_service.services.livekit_client import cleanup_livekit_service
from orchestrator_service.api.summary_api import internal_router as summary_internal_router, client_router as summary_client_router

# Load config
config = get_config()
logger = get_logger(__name__)
from dotenv import load_dotenv


load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== STARTUP =====
    logger.info("FastAPI startup") 
    mongodb = get_mongodb_service()
    ok = await mongodb.connect()
    if not ok:
        raise RuntimeError("❌ MongoDB connection failed on startup")
    logger.info("✅ MongoDB connected on startup")
    yield
    # ===== SHUTDOWN =====
    logger.info("FastAPI shutting down, cleaning up resources...")
    await egress_service.cleanup()
    await cleanup_livekit_service()
    
    # Disconnect Mongo LAST
    if mongodb is not None:
        await mongodb.disconnect()
    logger.info("All services cleanup completed")


app = FastAPI(title="LiveKit Orchestrator API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,  # Allow cookies to be included in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],     # Allow all headers
)


# Include routers
app.include_router(dispatch_router, prefix="/api")
app.include_router(tts_router, prefix="/api")
app.include_router(stream_router, prefix="/api")
app.include_router(chat_external_router, prefix="/api")
app.include_router(webhook_router, prefix="/api/webhook", tags=["webhook"])
app.include_router(room_router)  # Has prefix="/api/transcripts/rooms"
app.include_router(track_router)  # Has prefix="/api/transcripts/tracks"
app.include_router(transcript_router)  # Has prefix="/api/transcripts"
app.include_router(agent_control_router)  # Has prefix="/api/agent-control"
app.include_router(room_registry_router)  # Has prefix="/api/room-registry"
app.include_router(summary_internal_router)
app.include_router(summary_client_router)


