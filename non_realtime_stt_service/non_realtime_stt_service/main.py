"""
STT Non-Realtime Service

Standalone Whisper-based batch transcription worker.
Consumes tasks from Redis Stream, downloads audio from MinIO,
transcribes with Whisper + VAD, and pushes results back to Redis.
"""

import os
import time
import logging
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from dotenv import load_dotenv

from non_realtime_stt_service.service.metrics_service import metrics
from non_realtime_stt_service.service.health_service import get_health_service
from non_realtime_stt_service.service.redis.connection_pool import get_connection_manager
from non_realtime_stt_service.service.redis.redis_transcription_queue_service import (
    RedisTranscriptionQueueService,
)
from non_realtime_stt_service.service.whisper_transcription_processor import (
    transcribe_task,
    WhisperTranscriptionProcessor,
)
from non_realtime_stt_service.config import get_config
from non_realtime_stt_service.utils.logging_config import setup_logging

# Load environment variables
load_dotenv()

# Setup logging
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level)
logger = logging.getLogger(__name__)


# System metrics background updater (CPU, memory)
async def system_metrics_loop():
    config = get_config()
    
    if not config.metrics.enabled or not config.metrics.system_metrics:
        logger.info("System metrics disabled, skipping metrics loop")
        return
    
    try:
        import psutil
        have_psutil = True
    except Exception:
        psutil = None
        have_psutil = False
        
    update_interval = config.metrics.update_interval
    logger.info(f"System metrics loop started (interval: {update_interval}s)")
    
    while True:
        try:
            if have_psutil:
                try:
                    metrics.cpu_usage.set(psutil.cpu_percent(interval=None))
                    vm = psutil.virtual_memory()
                    metrics.memory_usage.set(getattr(vm, 'used', 0))
                except Exception as e:
                    logger.debug(f"System metrics (cpu/mem) error: {e}")
        except Exception as e:
            logger.debug(f"System metrics loop error: {e}")
        await asyncio.sleep(update_interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → Shutdown lifecycle for non-realtime STT worker."""
    logger.info("🚀 Starting STT Non-Realtime Service...")

    # 1. Connect Redis Connection Pool
    redis_manager = get_connection_manager()
    await redis_manager.connect()

    # 2. Load ASR engines (Whisper + Gipformer fallback)
    whisper_processor = WhisperTranscriptionProcessor()
    await whisper_processor.initialize()
    health_service = get_health_service()
    health_service.register_health_check(
        "gipformer_fallback",
        whisper_processor.gipformer_health_status,
    )

    # 3. Start Redis stream consumer after model assets are ready
    transcription_queue = RedisTranscriptionQueueService()
    transcription_queue.set_processor(transcribe_task)
    await transcription_queue.start()
    
    # 4. Optional background metrics loop
    system_metrics_task = asyncio.create_task(system_metrics_loop())
    
    logger.info("✅ STT Non-Realtime Service fully initialized and ready for tasks")
    
    yield
    
    # ===== SHUTDOWN =====
    logger.info("🛑 Shutting down STT Non-Realtime Service...")
    if transcription_queue is not None:
        await transcription_queue.stop()
    if whisper_processor is not None:
        await whisper_processor.shutdown()
    get_health_service().unregister_health_check("gipformer_fallback")
    try:
        await redis_manager.disconnect()
    except Exception as e:
        logger.warning("Redis pool disconnect: %s", e)
    
    system_metrics_task.cancel()
    try:
        await system_metrics_task
    except asyncio.CancelledError:
        pass
    logger.info("✅ STT Non-Realtime Service shutdown complete")


app = FastAPI(title="STT Non-Realtime Service", lifespan=lifespan)


@app.get("/health")
async def health_check():
    """Detailed health check endpoint."""
    health_service = get_health_service()
    health_status = health_service.get_health_status()
    
    status_code = 200
    if health_status.status == "unhealthy":
        status_code = 503
    elif health_status.status == "degraded":
        status_code = 200
    
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


@app.get("/metrics")
async def prometheus_metrics():
    """Prometheus metrics endpoint."""
    config = get_config()
    if not config.metrics.enabled:
        return Response("Metrics disabled", status_code=404)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
