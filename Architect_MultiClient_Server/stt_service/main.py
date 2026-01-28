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
    if os.getenv('PHASE_2', 'false').upper() == 'TRUE':                  # Removed when moving to phase 2.
        logger.info("✅ Whisper transcription enabled - initializing...")           # Removed when moving to phase 2.
        # Start transcription queue service with Whisper processor
        transcription_queue = get_transcription_queue_service()
        transcription_queue.set_processor(transcribe_task)  # Set Whisper processor
        await transcription_queue.start()
        
        # Pre-initialize Whisper model (optional, can be lazy loaded)
        whisper_processor = get_whisper_processor()
        await whisper_processor.initialize()
        logger.info("✅ Whisper transcription initialized successfully")           # Removed when moving to phase 2.
    else:   # Removed when moving to phase 2.
        logger.info("⚠️ Whisper transcription disabled - skipping initialization")   # Removed when moving to phase 2.
        transcription_queue = None                                                     # Removed when moving to phase 2.
        whisper_processor = None                                                        # Removed when moving to phase 2.
    
    system_metrics_task = asyncio.create_task(system_metrics_loop())
    
    yield
    
    # Shutdown
    if transcription_queue is not None:     # Removed when moving to phase 2.
        await transcription_queue.stop()
    if whisper_processor is not None:       # Removed when moving to phase 2.
        await whisper_processor.shutdown()
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


# HTTP metrics middleware for Prometheus
@app.middleware("http")
async def prometheus_http_middleware(request: Request, call_next):
    start_time = time.time()
    response = await call_next(request)
    
    # Check if HTTP metrics are enabled
    config = get_config()
    if config.metrics.enabled and config.metrics.http_metrics:
        try:
            path = request.url.path
            method = request.method
            status = getattr(response, 'status_code', 200)
            metrics.http_requests_total.labels(method=method, endpoint=path, status=status).inc()
            duration = time.time() - start_time
            metrics.http_request_duration.labels(endpoint=path).observe(duration)
        except Exception as e:
            logger.debug(f"Prometheus HTTP middleware error: {e}")
    return response

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



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False
    )