"""
Pipeline Manager for Per-Client Inference Pipelines
Manages lifecycle of individual client pipelines with resource limits.
"""

import asyncio
import logging
import threading
import time
from typing import Dict, List, Optional, Any, Callable
from collections import defaultdict
from vosk import Model

from .client_pipeline import ClientInferencePipeline, PipelineState
from ..config import get_config
from ..utils.circuit_breaker import get_stt_circuit_breaker

logger = logging.getLogger(__name__)


class PipelineManagerError(Exception):
    """Pipeline manager specific errors"""
    pass


class ClientLimitExceededError(PipelineManagerError):
    """Raised when maximum client limit is exceeded"""
    pass


class PipelineManager:
    """Manages per-client inference pipelines with resource limits"""
    
    def __init__(self, model_path: str, result_callback: Callable[[str, Dict], None]):
        self.config = get_config()
        self.result_callback = result_callback
        
        # Load Vosk model
        logger.info(f"Loading Vosk model from {model_path}")
        self.model = Model(model_path)
        logger.info("Vosk model loaded successfully")
        
        # Pipeline storage
        self.pipelines: Dict[str, ClientInferencePipeline] = {}  # client_key -> pipeline
        self.pipelines_lock = asyncio.Lock()
        
        # Resource limits
        self.max_clients = self.config.server.max_concurrent_clients
        
        # Statistics
        self.stats = {
            "total_created": 0,
            "total_destroyed": 0,
            "current_active": 0,
            "max_concurrent_reached": 0,
            "client_limit_rejections": 0,
            "idle_cleanups": 0
        }
        self.stats_lock = threading.Lock()
        
        # Cleanup configuration
        self.idle_timeout = 300.0  # 5 minutes idle timeout
        self.cleanup_interval = 60.0  # Cleanup every minute
        
        # Background tasks
        self._cleanup_task: Optional[asyncio.Task] = None
        self._metrics_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        logger.info(f"Pipeline manager initialized with max_clients={self.max_clients}")
    
    def _make_client_key(self, client_id: str, session_id: str) -> str:
        """Create unique key for client"""
        return f"{session_id}:{client_id}"
    
    async def start(self):
        """Start the pipeline manager"""
        logger.info("Starting pipeline manager...")
        
        # Start background tasks
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._metrics_task = asyncio.create_task(self._metrics_loop())
        
        logger.info("Pipeline manager started")
    
    async def create_pipeline(self, client_id: str, session_id: str) -> ClientInferencePipeline:
        """Create a new pipeline for a client"""
        client_key = self._make_client_key(client_id, session_id)
        
        async with self.pipelines_lock:
            # Check if pipeline already exists
            if client_key in self.pipelines:
                existing = self.pipelines[client_key]
                if existing.state not in [PipelineState.TERMINATED, PipelineState.SHUTTING_DOWN]:
                    logger.warning(f"Pipeline already exists for {client_key}")
                    return existing
                else:
                    # Clean up old terminated pipeline
                    await self._remove_pipeline(client_key, existing)
            
            # Check client limit
            if len(self.pipelines) >= self.max_clients:
                with self.stats_lock:
                    self.stats["client_limit_rejections"] += 1
                
                # Try to clean up idle pipelines to make room
                cleaned = await self._cleanup_idle_pipelines(force=True)
                
                if len(self.pipelines) >= self.max_clients:
                    raise ClientLimitExceededError(
                        f"Maximum client limit ({self.max_clients}) exceeded. "
                        f"Current active: {len(self.pipelines)}, Cleaned: {cleaned}"
                    )
            
            # Create new pipeline
            pipeline = ClientInferencePipeline(
                client_id=client_id,
                session_id=session_id,
                model=self.model,
                result_callback=self.result_callback,
                pipeline_manager=self  # Pass reference for adaptive chunk sizing
            )
            
            # Store and start pipeline
            self.pipelines[client_key] = pipeline
            await pipeline.start()
            
            # Update statistics
            with self.stats_lock:
                self.stats["total_created"] += 1
                self.stats["current_active"] = len(self.pipelines)
                if self.stats["current_active"] > self.stats["max_concurrent_reached"]:
                    self.stats["max_concurrent_reached"] = self.stats["current_active"]
            
            logger.info(f"Created pipeline for {client_key} ({len(self.pipelines)}/{self.max_clients} slots used)")
            return pipeline
    
    async def get_pipeline(self, client_id: str, session_id: str) -> Optional[ClientInferencePipeline]:
        """Get existing pipeline for a client"""
        client_key = self._make_client_key(client_id, session_id)
        
        async with self.pipelines_lock:
            return self.pipelines.get(client_key)
    
    async def remove_pipeline(self, client_id: str, session_id: str, shutdown_timeout: float = 5.0):
        """Remove and shutdown a client pipeline"""
        client_key = self._make_client_key(client_id, session_id)
        
        async with self.pipelines_lock:
            if client_key in self.pipelines:
                pipeline = self.pipelines[client_key]
                await self._remove_pipeline(client_key, pipeline, shutdown_timeout)
                logger.info(f"Removed pipeline for {client_key}")
            else:
                logger.debug(f"No pipeline found for {client_key} to remove")
    
    async def _remove_pipeline(self, client_key: str, pipeline: ClientInferencePipeline, 
                              shutdown_timeout: float = 5.0):
        """Internal method to remove and shutdown pipeline"""
        try:
            # Shutdown pipeline gracefully
            await pipeline.shutdown(timeout=shutdown_timeout)
        except Exception as e:
            logger.error(f"Error shutting down pipeline {client_key}: {e}")
        finally:
            # Remove from storage
            self.pipelines.pop(client_key, None)
            
            # Update statistics
            with self.stats_lock:
                self.stats["total_destroyed"] += 1
                self.stats["current_active"] = len(self.pipelines)
    
    async def submit_audio(self, client_id: str, session_id: str, audio_chunk: bytes) -> bool:
        """Submit audio to client pipeline"""
        pipeline = await self.get_pipeline(client_id, session_id)
        
        if not pipeline:
            logger.warning(f"No pipeline found for client {client_id} in session {session_id}")
            return False
        
        return await pipeline.submit_audio(audio_chunk)
    
    async def _cleanup_loop(self):
        """Background cleanup loop for idle pipelines"""
        logger.info("Cleanup loop started")
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), 
                        timeout=self.cleanup_interval
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    # Perform periodic cleanup
                    await self._cleanup_idle_pipelines()
                    
        except asyncio.CancelledError:
            logger.info("Cleanup loop cancelled")
        except Exception as e:
            logger.error(f"Error in cleanup loop: {e}")
        finally:
            logger.info("Cleanup loop terminated")
    
    async def _cleanup_idle_pipelines(self, force: bool = False) -> int:
        """Clean up idle pipelines"""
        cleaned_count = 0
        idle_threshold = 30.0 if force else self.idle_timeout
        
        async with self.pipelines_lock:
            to_remove = []
            
            for client_key, pipeline in self.pipelines.items():
                if pipeline.is_idle(idle_threshold) or pipeline.state == PipelineState.TERMINATED:
                    to_remove.append((client_key, pipeline))
            
            # Remove idle pipelines
            for client_key, pipeline in to_remove:
                try:
                    await self._remove_pipeline(client_key, pipeline, shutdown_timeout=2.0)
                    cleaned_count += 1
                    logger.info(f"Cleaned up idle pipeline: {client_key}")
                except Exception as e:
                    logger.error(f"Error cleaning up pipeline {client_key}: {e}")
        
        if cleaned_count > 0:
            with self.stats_lock:
                self.stats["idle_cleanups"] += cleaned_count
            
            logger.info(f"Cleaned up {cleaned_count} idle pipelines")
        
        return cleaned_count
    
    async def _metrics_loop(self):
        """Background metrics logging loop"""
        logger.info("Metrics loop started")
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), 
                        timeout=self.config.stt.metrics_interval_sec
                    )
                    break  # Shutdown requested
                except asyncio.TimeoutError:
                    # Log metrics
                    await self._log_metrics()
                    
        except asyncio.CancelledError:
            logger.info("Metrics loop cancelled")
        except Exception as e:
            logger.error(f"Error in metrics loop: {e}")
        finally:
            logger.info("Metrics loop terminated")
    
    async def _log_metrics(self):
        """Log current metrics"""
        try:
            async with self.pipelines_lock:
                active_count = len(self.pipelines)
                pipeline_states = defaultdict(int)
                total_chunks = 0
                total_results = 0
                
                for pipeline in self.pipelines.values():
                    pipeline_states[pipeline.state.value] += 1
                    stats = pipeline.get_stats()
                    total_chunks += stats.chunks_processed
                    total_results += stats.final_results + stats.partial_results
            
            with self.stats_lock:
                stats_copy = self.stats.copy()
            
            logger.info(
                f"Pipeline Metrics | Active: {active_count}/{self.max_clients} | "
                f"States: {dict(pipeline_states)} | "
                f"Total Created: {stats_copy['total_created']} | "
                f"Total Destroyed: {stats_copy['total_destroyed']} | "
                f"Rejections: {stats_copy['client_limit_rejections']} | "
                f"Cleanups: {stats_copy['idle_cleanups']} | "
                f"Chunks Processed: {total_chunks} | "
                f"Results Generated: {total_results}"
            )
            
        except Exception as e:
            logger.error(f"Error logging metrics: {e}")
    
    async def shutdown(self, timeout: float = 10.0):
        """Shutdown the pipeline manager"""
        logger.info("Shutting down pipeline manager...")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Cancel background tasks
        if self._cleanup_task:
            self._cleanup_task.cancel()
        if self._metrics_task:
            self._metrics_task.cancel()
        
        # Wait for background tasks
        tasks = [t for t in [self._cleanup_task, self._metrics_task] if t]
        if tasks:
            try:
                await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=2.0)
            except asyncio.TimeoutError:
                logger.warning("Background tasks shutdown timeout")
        
        # Shutdown all pipelines
        async with self.pipelines_lock:
            shutdown_tasks = []
            for client_key, pipeline in list(self.pipelines.items()):
                shutdown_tasks.append(self._remove_pipeline(client_key, pipeline, shutdown_timeout=3.0))
            
            if shutdown_tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*shutdown_tasks, return_exceptions=True), timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("Pipeline shutdown timeout")
        
        logger.info("Pipeline manager shutdown complete")
    
    def get_stats(self) -> Dict[str, Any]:
        """Get current statistics"""
        with self.stats_lock:
            stats_copy = self.stats.copy()
        
        return {
            "manager_stats": stats_copy,
            "active_pipelines": len(self.pipelines),
            "max_clients": self.max_clients,
            "idle_timeout": self.idle_timeout,
            "cleanup_interval": self.cleanup_interval
        }
    
    def get_all_pipeline_info(self) -> List[Dict[str, Any]]:
        """Get information about all active pipelines"""
        pipeline_info = []
        
        # Create a snapshot to avoid holding lock too long
        pipelines_snapshot = list(self.pipelines.values())
        
        for pipeline in pipelines_snapshot:
            try:
                pipeline_info.append(pipeline.get_info())
            except Exception as e:
                logger.error(f"Error getting pipeline info: {e}")
        
        return pipeline_info
    
    async def get_pipeline_health(self) -> Dict[str, Any]:
        """Get pipeline health information"""
        async with self.pipelines_lock:
            active_count = len(self.pipelines)
            
            state_counts = defaultdict(int)
            for pipeline in self.pipelines.values():
                state_counts[pipeline.state.value] += 1
        
        with self.stats_lock:
            stats_copy = self.stats.copy()
        
        utilization = (active_count / self.max_clients) * 100 if self.max_clients > 0 else 0
        
        # Determine health status
        if utilization >= 95:
            health_status = "critical"
        elif utilization >= 80:
            health_status = "warning"
        elif stats_copy["client_limit_rejections"] > 0:
            health_status = "degraded"
        else:
            health_status = "healthy"
        
        return {
            "status": health_status,
            "active_pipelines": active_count,
            "max_clients": self.max_clients,
            "utilization_percent": round(utilization, 1),
            "pipeline_states": dict(state_counts),
            "statistics": stats_copy,
            "timestamp": time.time()
        }