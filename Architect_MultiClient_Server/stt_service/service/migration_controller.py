"""
Per-Client Pipeline Service Controller
Manages the new per-client inference pipeline architecture.
"""

import logging
from typing import Optional, Any, Dict


logger = logging.getLogger(__name__)


class PipelineServiceController:
    """Controls the new per-client pipeline architecture"""
    
    def __init__(self):
        # Initialize the new pipeline-based service
        from stt_service.service.new_vosk_service import stt_service_new
        self.pipeline_service = stt_service_new
        self.result_dispatcher = None  # NEW: For optimized dispatch
        
        logger.info("Pipeline service controller initialized with per-client architecture")
    
    def get_service(self):
        """Get the pipeline service"""
        return self.pipeline_service
    
    async def submit_audio_async(self, chunk: bytes, client_id: str, session_id: str, chunk_id: Optional[int] = None) -> bool:
        """Submit audio using pipeline service"""
        return await self.pipeline_service.submit_audio_async(chunk, client_id, session_id, chunk_id=chunk_id)
    
    async def cleanup_client(self, client_id: str, session_id: str):
        """Cleanup client using pipeline service"""
        await self.pipeline_service.cleanup_client(client_id, session_id)
    
    def get_active_clients_info(self) -> Dict[str, Any]:
        """Get active clients info from pipeline service"""
        info = self.pipeline_service.get_active_clients_info()
        info["architecture"] = "per_client_pipelines"
        return info
    
    def get_circuit_breaker_status(self) -> Dict[str, Any]:
        """Get circuit breaker status from pipeline service"""
        return self.pipeline_service.get_circuit_breaker_status()
    
    def get_pipeline_distribution(self) -> Dict[str, Any]:
        """Get pipeline distribution (replaces worker distribution)"""
        try:
            pipeline_info = self.pipeline_service.pipeline_manager.get_all_pipeline_info()
            stats = self.pipeline_service.get_stats()
            
            return {
                "pipeline_distribution": {
                    "total_pipelines": len(pipeline_info),
                    "active_pipelines": len([p for p in pipeline_info if p["state"] == "active"]),
                    "idle_pipelines": len([p for p in pipeline_info if p["state"] == "idle"]),
                    "max_clients": self.pipeline_service.config.server.max_concurrent_clients,
                    "pipeline_details": pipeline_info
                },
                "manager_stats": stats["manager_stats"] if "manager_stats" in stats else {},
                "distribution_quality": "per_client_dedicated",
                "architecture": "per_client_pipelines",
                "timestamp": __import__('time').time()
            }
        except Exception as e:
            logger.error(f"Error getting pipeline distribution: {e}")
            return {"error": str(e), "architecture": "per_client_pipelines"}
    
    def set_result_dispatcher(self, dispatcher):
        """Set the optimized result dispatcher"""
        self.result_dispatcher = dispatcher
        self.pipeline_service.set_result_dispatcher(dispatcher)
        logger.info("✅ PipelineController configured with OptimizedResultDispatcher")
    
    def set_async_result_queue(self, loop, async_queue):
        """DEPRECATED - kept for backward compatibility"""
        logger.warning("⚠️ set_async_result_queue() is DEPRECATED! Use set_result_dispatcher()")
        # Do nothing - new architecture doesn't use shared queue
    
    async def start_service(self):
        """Start the pipeline service"""
        await self.pipeline_service.start()
        logger.info("Per-client pipeline service started")
    
    async def shutdown_service(self):
        """Shutdown pipeline service"""
        try:
            await self.pipeline_service.shutdown()
            logger.info("Per-client pipeline service shutdown complete")
        except Exception as e:
            logger.error(f"Error shutting down pipeline service: {e}")
    
    def get_service_info(self) -> Dict[str, Any]:
        """Get information about current service"""
        return {
            "service_type": "per_client_pipelines",
            "architecture": "dedicated_pipeline_per_client",
            "features": [
                "Individual Vosk recognizer per client",
                "Dedicated audio buffer per client", 
                "Independent processing task per client",
                "Complete client isolation",
                "Configurable client limits"
            ],
            "service_initialized": self.pipeline_service is not None
        }
    
    async def get_pipeline_health(self) -> Dict[str, Any]:
        """Get pipeline health information"""
        return await self.pipeline_service.get_pipeline_health()


# Global pipeline service controller instance
pipeline_controller = PipelineServiceController()