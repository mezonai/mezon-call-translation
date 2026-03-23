from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.mongodb_service import MongoDBService
from orchestrator_service.config.application_config import get_config
from contextlib import asynccontextmanager

from orchestrator_service.api.dispatch_api import router as dispatch_router
from orchestrator_service.api.sse_transcript_api import router as stream_router
from orchestrator_service.api.sse_chat_external_api import router as sse_chat_external_router
from orchestrator_service.api.sse_metadata_api import router as sse_metadata_router
from orchestrator_service.api.sse_agent_request_api import router as sse_agent_request_router
from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.webhook_api import router as webhook_router, egress_service
from orchestrator_service.api.room_api import router as room_router
from orchestrator_service.api.track_api import router as track_router
from orchestrator_service.api.transcript_api import router as transcript_router
from orchestrator_service.api.room_registry_api import router as room_registry_router
from orchestrator_service.api.queue_api import router as queue_router
from orchestrator_service.api.auth_api import router as auth_router
from orchestrator_service.services.livekit_client import cleanup_livekit_service
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.redis.connection_pool import get_connection_manager
from orchestrator_service.api.summary_api import internal_router as summary_internal_router, client_router as summary_client_router
from orchestrator_service.services.redis.redis_save_transcription_service import RedisSaveTranscriptionService

import signal

# Load config
config = get_config()
logger = get_logger(__name__)
sse_manager = SSEManager()  # Get singleton instance
original_sigint = signal.getsignal(signal.SIGINT)
original_sigterm = signal.getsignal(signal.SIGTERM)

def signal_exit(signum, frame):
    """
    Signal handler for SIGINT (Ctrl+C) and SIGTERM.
    
    This is called SYNCHRONOUSLY when signal is received.
    Cannot use 'await' here, so we call synchronous method on sse_manager.
    """
    signal_name = "SIGINT" if signum == signal.SIGINT else "SIGTERM"
    logger.info(f"🛑 Received {signal_name}, initiating graceful shutdown...")
    
    # Call synchronous method to notify all SSE connections
    # This sends shutdown messages to all queues without awaiting
    sse_manager.signal_shutdown()
    
    if callable(original_sigint):
        original_sigint(signum, frame)
    # Note: We DON'T call sys.exit() here
    # Let Uvicorn's signal handler run after this to properly shutdown


# Register signal handlers
signal.signal(signal.SIGINT, signal_exit)
signal.signal(signal.SIGTERM, signal_exit)
logger.info("✅ Signal handlers registered for SIGINT and SIGTERM")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ===== STARTUP =====
    logger.info("🚀 FastAPI startup")
    mongodb = MongoDBService()
    ok = await mongodb.connect()
    if not ok:
        raise RuntimeError("❌ MongoDB connection failed on startup")
    logger.info("✅ MongoDB connected on startup")

    # Connect Redis Connection Pool (shared by all repositories)
    try:
        redis_manager = get_connection_manager()
        await redis_manager.connect()
        logger.info("✅ Redis connection pool created")
        
        # Initialize Room Registry (auto-connects to Redis pool)
        room_registry = get_room_registry()
        logger.info("✅ Room Registry initialized")
        
        # Initialize Save Transcription consumer service
        save_transcription_service = RedisSaveTranscriptionService()
        await save_transcription_service.start()
        logger.info("✅ Save Transcription consumer service started")
    except Exception as e:
        logger.error(f"❌ Failed to initialize Redis services: {e}")
        raise
    
    yield
    
    # ===== SHUTDOWN =====
    # Uvicorn automatically cancels all active SSE generators when shutdown signal received
    # We just need to cleanup resources after generators are cancelled
    logger.info("🛑 FastAPI shutting down, cleaning up resources...")
    
    # Step 0: Stop save transcription service
    try:
        logger.info("Step 0/6: Stopping Save Transcription service...")
        save_transcription_service = RedisSaveTranscriptionService()
        await save_transcription_service.stop()
        logger.info("✅ Save Transcription service stopped")
    except Exception as e:
        logger.error(f"Error stopping Save Transcription service: {e}")
    
    # Step 1: Cleanup SSE manager (clear data structures)
    # SSE connections were already notified by signal handler
    logger.info("Step 1/6: Cleaning up SSE manager...")
    await sse_manager.cleanup()
    logger.info("✅ SSE manager cleanup completed")
    
    # Step 2: Cleanup egress service
    logger.info("Step 2/6: Cleaning up egress service...")
    await egress_service.cleanup()
    logger.info("✅ Egress service cleanup completed")
    
    # Step 3: Cleanup LiveKit service
    logger.info("Step 3/6: Cleaning up LiveKit service...")
    await cleanup_livekit_service()
    logger.info("✅ LiveKit service cleanup completed")
    
    # Step 4: Disconnect Redis Connection Pool
    try:
        logger.info("Step 4/6: Disconnecting Redis connection pool...")
        redis_manager = get_connection_manager()
        await redis_manager.disconnect()
        logger.info("✅ Redis connection pool closed")
    except Exception as e:
        logger.error(f"Error closing Redis connection pool: {e}")
    
    # Step 5: Disconnect MongoDB LAST
    logger.info("Step 5/6: Disconnecting MongoDB...")
    if mongodb is not None:
        await mongodb.disconnect()
    logger.info("✅ MongoDB disconnected")
        # Disconnect Redis Connection Pool

    logger.info("🎉 All services cleanup completed successfully")


app = FastAPI(title="LiveKit Orchestrator API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,  # Allow cookies to be included in cross-origin requests
    allow_methods=["*"],     # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"],     # Allow all headers
)


# Include routers
app.include_router(auth_router)  # Mezon OAuth2 authentication
app.include_router(dispatch_router, prefix="/api")
app.include_router(stream_router, prefix="/api", tags=["sse transcript"])
app.include_router(sse_chat_external_router, prefix="/api", tags=["sse chat external"])
app.include_router(sse_metadata_router, prefix="/api", tags=["sse metadata"])
app.include_router(sse_agent_request_router, prefix="/api", tags=["sse agent requests"])
app.include_router(webhook_router, prefix="/api/webhook", tags=["webhook"])
app.include_router(queue_router)  # Has prefix="/api/queue"
app.include_router(room_router)  # Has prefix="/api/transcripts/rooms"
app.include_router(track_router)  # Has prefix="/api/transcripts/tracks"
app.include_router(transcript_router)  # Has prefix="/api/transcripts"
app.include_router(room_registry_router)  # Has prefix="/api/room-registry"
app.include_router(summary_internal_router)
app.include_router(summary_client_router)


