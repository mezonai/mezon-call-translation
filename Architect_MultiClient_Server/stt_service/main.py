import os
import time
import logging
from fastapi import FastAPI
import asyncio
from contextlib import asynccontextmanager
from .service.metrics_service import metrics
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST

# Import relative to Server package
from stt_service.controller.ws_vosk_control import router as stt_router
from stt_service.controller.transcription_api import router as transcription_router
from stt_service.service.migration_controller import pipeline_controller
from stt_service.service.health_service import get_health_service
from stt_service.service.transcription_queue_service import get_transcription_queue_service
from stt_service.service.whisper_transcription_processor import transcribe_task, get_whisper_processor
from stt_service.service.summary_queue_service import get_summary_queue_service
from stt_service.service.summary_processor import get_summary_processor
from stt_service.config import get_config
from .utils.logging_config import setup_logging
from dotenv import load_dotenv

from fastapi import FastAPI, Response, Request
from fastapi.middleware.cors import CORSMiddleware


# Load environment variables
load_dotenv()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup → Shutdown lifecycle."""


    # Initialize optimized dispatcher
    from .service.result_dispatcher import get_result_dispatcher
    result_dispatcher = get_result_dispatcher(metrics=metrics)
    
    # Start pipeline service
    await pipeline_controller.start_service()
    
    # Set dispatcher (replaces set_async_result_queue)
    pipeline_controller.set_result_dispatcher(result_dispatcher)
    
    # Initialize Whisper transcription if enabled
    transcription_queue = get_transcription_queue_service()
    transcription_queue.set_processor(transcribe_task)  # Set Whisper processor
    await transcription_queue.start()
        
    # Pre-initialize Whisper model (optional, can be lazy loaded)
    whisper_processor = get_whisper_processor()
    await whisper_processor.initialize()
    
    # Initialize Summary Queue Service
    summary_queue = get_summary_queue_service()
    summary_processor = get_summary_processor()
    summary_queue.set_processor(summary_processor.process_summary)
    await summary_queue.start()
    logger.info("✅ Summary queue service started")
    
    system_metrics_task = asyncio.create_task(system_metrics_loop())
    
    yield
    
    # Shutdown
    if transcription_queue is not None:
        await transcription_queue.stop()
    if whisper_processor is not None:
        await whisper_processor.shutdown()
    
    # Stop summary queue
    if summary_queue is not None:
        await summary_queue.stop()
        logger.info("✅ Summary queue service stopped")
    
    await result_dispatcher.shutdown()
    await pipeline_controller.shutdown_service()
    
    
    system_metrics_task.cancel()
    try:
        await system_metrics_task
    except asyncio.CancelledError:
        pass


# Init logging and FastAPI
# Get log level from environment variable
log_level_str = os.getenv('LOG_LEVEL', 'INFO').upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level)
logger = logging.getLogger(__name__)

app = FastAPI(lifespan=lifespan)


# System metrics background updater (CPU, memory, queue sizes)
async def system_metrics_loop():
    config = get_config()
    
    # Skip if metrics disabled
    if not config.metrics.enabled or not config.metrics.system_metrics:
        logger.info("System metrics disabled, skipping metrics loop")
        return
    
    try:
        import psutil  # optional dependency
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
            # Queue size aggregation across pipelines
            try:
                info = pipeline_controller.get_active_clients_info()
                details = info.get('pipeline_details', []) or []
                total_qsize = 0
                for d in details:
                    try:
                        total_qsize += int(d.get('queue_size', 0))
                    except Exception:
                        continue
                metrics.queue_size.labels(queue_name='audio_queue').set(total_qsize)
            except Exception as e:
                logger.debug(f"Queue size metrics error: {e}")
            
            # Dispatcher queue metrics
            try:
                from .service.result_dispatcher import get_result_dispatcher
                dispatcher = get_result_dispatcher()
                dispatcher_stats = dispatcher.get_stats()
                total_dispatcher_queue = sum(
                    client.get('queue_size', 0)
                    for client in dispatcher_stats.get('clients', [])
                )
                metrics.queue_size.labels(queue_name='dispatcher_queue').set(total_dispatcher_queue)
            except Exception as e:
                logger.debug(f"Dispatcher queue metrics error: {e}")
        except Exception as e:
            logger.debug(f"System metrics loop error: {e}")
        await asyncio.sleep(update_interval)

# Prometheus metrics endpoint
@app.get("/metrics")
async def prometheus_metrics():
    config = get_config()
    if not config.metrics.enabled:
        return Response("Metrics disabled", status_code=404)
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)



app.include_router(stt_router)
app.include_router(transcription_router)

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

