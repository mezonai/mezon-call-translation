"""
New Per-Client Nemotron STT Service
Replaces the shared worker pool with dedicated inference pipelines per client.
"""

import time
import asyncio
import logging
from pathlib import Path
from typing import Dict, Optional

from .pipeline_manager import PipelineManager, ClientLimitExceededError
from .health_service import register_stt_health_checks
from ..utils.circuit_breaker import get_stt_circuit_breaker
from ..config import get_config

logger = logging.getLogger(__name__)


class NewSTTNemotronService:
    """Nemotron STT service with per-client inference pipelines."""
    
    def __init__(self, model_path: Optional[str] = None):
        logger.info("Initializing Nemotron STT Service with per-client pipelines...")
        
        # Get configuration
        self.config = get_config()
        
        # A relative configuration value is resolved under the project model folder.
        configured_path = model_path or self.config.stt.nemotron_model_path
        resolved_path = Path(configured_path)
        if not resolved_path.is_absolute():
            project_root = Path(__file__).resolve().parents[2]
            resolved_path = project_root / "models" / "nemotron-model" / resolved_path

        if not resolved_path.exists():
            raise FileNotFoundError(f"Nemotron model not found at {resolved_path}")
        
        # Result handling
        self.result_dispatcher = None  # NEW: For optimized dispatch
        self.async_result_queue = None  # Legacy: kept for compatibility
        self.async_loop = None  # Legacy: kept for compatibility
        
        # Initialize pipeline manager with dispatcher callback
        self.pipeline_manager = PipelineManager(
            model_path=str(resolved_path),
            result_callback=self._emit_result_via_dispatcher  # Changed to new method
        )
        
        # Circuit breaker
        self._circuit_breaker = get_stt_circuit_breaker()
        
        # Service state
        self._started = False
        
        logger.info("Nemotron STT Service initialized successfully")
    
    async def start(self):
        """Start the service"""
        if self._started:
            return
        
        await self.pipeline_manager.start()
        self._started = True
        
        # Register health checks
        register_stt_health_checks(self)
        
        logger.info("Nemotron STT Service started")
    
    def set_result_dispatcher(self, dispatcher):
        """Set the optimized result dispatcher"""
        self.result_dispatcher = dispatcher
        logger.info("✅ STT Service configured with OptimizedResultDispatcher")
    
    def set_async_result_queue(self, loop, async_queue):
        """DEPRECATED - Register asyncio loop and queue for non-polling result dispatch."""
        logger.warning("⚠️ set_async_result_queue() is DEPRECATED! Use set_result_dispatcher()")
        self.async_loop = loop
        self.async_result_queue = async_queue
    
    async def submit_audio_async(self, chunk: bytes, client_id: str, session_id: str, chunk_id: Optional[int] = None):
        """Submit audio chunk for processing, optional chunk_id for tracing"""
        if not self._started:
            await self.start()

        
        try:
            # Check circuit breaker
            if not self._circuit_breaker.can_try():
                circuit_state = self._circuit_breaker.get_state()
                time_since_failure = circuit_state.get('time_since_last_failure', 0)
                failure_count = circuit_state.get('failure_count', 0)
                
                logger.error(
                    f"🚨 Circuit breaker BLOCKING audio for client {client_id}! 🚨\n"
                    f"   Circuit State: {circuit_state.get('state')}\n"
                    f"   Failure Count: {failure_count}/{self._circuit_breaker.config.failure_threshold}\n"
                    f"   Time Since Last Failure: {time_since_failure:.1f}s\n"
                    f"   Last Failure: {time.strftime('%H:%M:%S', time.localtime(circuit_state.get('last_failure_time', 0))) if circuit_state.get('last_failure_time') else 'Unknown'}\n"
                    f"   ⚠️  All audio processing is suspended until circuit recovers"
                )
                return False
            
            # Get or create pipeline for client
            pipeline = await self.pipeline_manager.get_pipeline(client_id, session_id)
            
            if not pipeline:
                try:
                    pipeline = await self.pipeline_manager.create_pipeline(client_id, session_id)
                    # pass

                    logger.info(f"Created new pipeline for client {client_id} in session {session_id}")
                except ClientLimitExceededError as e:
                    logger.error(
                        f"🚫 CIRCUIT BREAKER FAILURE: Client limit exceeded for {client_id}\n"
                        f"   Reason: {e}\n"
                        f"   This failure will count toward circuit breaker threshold\n"
                        f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}"
                    )
                    self._circuit_breaker.record_failure()
                    return False
            
            # Submit audio to pipeline (propagate optional chunk_id)
            success = await pipeline.submit_audio(chunk, chunk_id=chunk_id)

            
            if success:
                self._circuit_breaker.record_success()
            else:
                logger.warning(
                    f"🚫 CIRCUIT BREAKER FAILURE: Pipeline rejected audio for client {client_id}\n"
                    f"   Reason: Pipeline submit_audio returned False\n"
                    f"   Possible causes: Pipeline state (TERMINATED/SHUTTING_DOWN), queue full, or processing error\n"
                    f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}"
                )
                self._circuit_breaker.record_failure()
            
            return success
            
        except Exception as e:
            logger.error(
                f"🚫 CIRCUIT BREAKER FAILURE: Exception in audio submission for client {client_id}\n"
                f"   Exception: {type(e).__name__}: {e}\n"
                f"   This indicates a system-level error in audio processing\n"
                f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}",
                exc_info=True
            )
            self._circuit_breaker.record_failure()
            return False
    
    def _emit_result_via_dispatcher(self, result_type: str, payload: Dict):
        """Thread-safe result emission via optimized dispatcher"""
        if self.result_dispatcher is not None:
            try:
                client_id = payload.get('client_id')
                session_id = payload.get('session_id')
                
                coro = self.result_dispatcher.emit_result(
                    session_id=session_id,
                    client_id=client_id,
                    result_type=result_type,
                    payload=payload
                )
                
                try:
                    loop = asyncio.get_event_loop()
                    asyncio.run_coroutine_threadsafe(coro, loop)
                except RuntimeError:
                    loop = asyncio.get_running_loop()
                    asyncio.run_coroutine_threadsafe(coro, loop)
                
                logger.debug(f"Emitted result via optimized dispatcher: {result_type}")
                
            except Exception as e:
                logger.error(f"Failed to emit via dispatcher: {e}")
                self._fallback_emit_result(result_type, payload)
        else:
            self._fallback_emit_result(result_type, payload)
    
    def _fallback_emit_result(self, result_type: str, payload: Dict):
        """Fallback using legacy queue"""
        logger.debug(f"Using legacy result emission - {result_type}: '{payload.get('text', '')}'")
        
        if self.async_result_queue is not None and self.async_loop is not None:
            try:
                self.async_loop.call_soon_threadsafe(
                    self.async_result_queue.put_nowait, 
                    (result_type, payload)
                )
                logger.debug("Successfully queued result using legacy method")
            except Exception as e:
                logger.error(f"Failed to emit result (legacy): {e}")
        else:
            logger.error(f"No result output configured! Queue={self.async_result_queue}, Loop={self.async_loop}")
    
    async def cleanup_client(self, client_id: str, session_id: str):
        """Cleanup client resources when client disconnects."""
        try:
            await self.pipeline_manager.remove_pipeline(client_id, session_id)
            logger.info(f"Cleaned up client {client_id} from session {session_id}")
        except Exception as e:
            logger.error(f"Error cleaning up client {client_id}: {e}")
    
    def get_active_clients_info(self):
        """Get information about currently active clients."""
        try:
            pipeline_info = self.pipeline_manager.get_all_pipeline_info()
            
            # Count active pipelines by state
            total_active = len(pipeline_info)
            active_by_state = {}
            
            for info in pipeline_info:
                state = info.get("state", "unknown")
                active_by_state[state] = active_by_state.get(state, 0) + 1
            
            return {
                "total_active_pipelines": total_active,
                "max_concurrent_clients": self.config.server.max_concurrent_clients,
                "pipelines_by_state": active_by_state,
                "pipeline_details": pipeline_info,
                "timestamp": time.time()
            }
            
        except Exception as e:
            logger.error(f"Error getting active clients info: {e}")
            return {"error": str(e)}
    
    def get_circuit_breaker_status(self):
        """Get circuit breaker status for debugging."""
        try:
            return {
                "state": self._circuit_breaker.state.name,
                "failure_count": self._circuit_breaker.failure_count,
                "last_failure_time": getattr(self._circuit_breaker, 'last_failure_time', None),
                "disconnecting": getattr(self._circuit_breaker, '_disconnecting', False),
                "can_try": self._circuit_breaker.can_try(),
                "config": {
                    "failure_threshold": self._circuit_breaker.config.failure_threshold
                },
                "detailed_state": self._circuit_breaker.get_state()
            }
        except Exception as e:
            logger.error(f"Error getting circuit breaker status: {e}")
            return {"error": str(e), "status": "unknown"}
    
    async def get_pipeline_health(self):
        """Get pipeline health information"""
        return await self.pipeline_manager.get_pipeline_health()
    
    def get_stats(self):
        """Get service statistics"""
        return self.pipeline_manager.get_stats()
    
    # Legacy compatibility methods (kept for smooth transition)
    def submit_audio(self, chunk, client_id, session_id):
        """Legacy synchronous submit - converts to async call"""
        logger.warning("Using legacy submit_audio method - consider using submit_audio_async")
        
        # Run async method in new task
        try:
            loop = asyncio.get_event_loop()
            task = loop.create_task(self.submit_audio_async(chunk, client_id, session_id))
            return True  # Return immediately for compatibility
        except Exception as e:
            logger.error(f"Error in legacy submit_audio: {e}")
            return False
    
    def get_pipeline_distribution(self):
        """Get pipeline distribution information"""
        try:
            pipeline_info = self.pipeline_manager.get_all_pipeline_info()
            stats = self.pipeline_manager.get_stats()
            
            return {
                "pipeline_distribution": {
                    "total_pipelines": len(pipeline_info),
                    "active_pipelines": len([p for p in pipeline_info if p["state"] == "active"]),
                    "idle_pipelines": len([p for p in pipeline_info if p["state"] == "idle"]),
                    "max_clients": self.config.server.max_concurrent_clients,
                },
                "manager_stats": stats["manager_stats"],
                "distribution_quality": "per_client_dedicated",  # Always good with per-client
                "timestamp": time.time()
            }
        except Exception as e:
            logger.error(f"Error getting pipeline distribution: {e}")
            return {"error": str(e)}
    
    async def shutdown(self):
        """Shutdown the service gracefully"""
        logger.info("Shutting down Nemotron STT Service...")
        
        try:
            await self.pipeline_manager.shutdown(timeout=10.0)
            self._started = False
            logger.info("Nemotron STT Service shutdown complete")
        except Exception as e:
            logger.error(f"Error during service shutdown: {e}")
            raise


# Create service instance
stt_service_new = NewSTTNemotronService()
