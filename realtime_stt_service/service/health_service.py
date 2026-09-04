"""
Health Monitoring Service for system health checks and metrics.
"""
import time
import logging
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass
from fastapi import APIRouter, HTTPException
from ..utils.circuit_breaker import get_all_circuit_breaker_states

logger = logging.getLogger(__name__)

# Create router for health endpoints
router = APIRouter()

@dataclass
class HealthStatus:
    """Health status information."""
    status: str  # "healthy", "degraded", "unhealthy"
    timestamp: float
    details: Dict[str, Any]
    uptime: float


class HealthService:
    """Service for monitoring system health and providing health checks."""
    
    def __init__(self, start_time: float = None):
        self.start_time = start_time or time.time()
        self._health_checks: Dict[str, callable] = {}
        self._last_health_status: Optional[HealthStatus] = None
        self._lock = threading.RLock()
        
        # Register default health checks
        self._register_default_health_checks()
        
        logger.info("Health Service initialized")
    
    def _register_default_health_checks(self):
        """Register default health checks."""
        self.register_health_check("uptime", self._check_uptime)
        self.register_health_check("memory", self._check_memory)
        self.register_health_check("circuit_breakers", self._check_circuit_breakers)
    
    def register_health_check(self, name: str, check_func: callable):
        """Register a health check function."""
        with self._lock:
            self._health_checks[name] = check_func
            logger.debug(f"Registered health check: {name}")
    
    def unregister_health_check(self, name: str):
        """Unregister a health check function."""
        with self._lock:
            if name in self._health_checks:
                del self._health_checks[name]
                logger.debug(f"Unregistered health check: {name}")
    
    def get_health_status(self) -> HealthStatus:
        """Get current health status."""
        with self._lock:
            timestamp = time.time()
            uptime = timestamp - self.start_time
            
            # Run all health checks
            check_results = {}
            overall_status = "healthy"
            
            for name, check_func in self._health_checks.items():
                try:
                    result = check_func()
                    check_results[name] = result
                    
                    # Determine overall status based on individual checks
                    if isinstance(result, dict) and result.get("status") == "unhealthy":
                        overall_status = "unhealthy"
                    elif isinstance(result, dict) and result.get("status") == "degraded" and overall_status == "healthy":
                        overall_status = "degraded"
                        
                except Exception as e:
                    logger.error(f"Health check {name} failed: {e}")
                    check_results[name] = {
                        "status": "unhealthy",
                        "error": str(e)
                    }
                    overall_status = "unhealthy"
            
            # Create health status
            health_status = HealthStatus(
                status=overall_status,
                timestamp=timestamp,
                details=check_results,
                uptime=uptime
            )
            
            self._last_health_status = health_status
            return health_status
    
    def _check_uptime(self) -> Dict[str, Any]:
        """Check system uptime."""
        uptime = time.time() - self.start_time
        return {
            "status": "healthy",
            "uptime_seconds": uptime,
            "uptime_hours": uptime / 3600,
            "start_time": self.start_time
        }
    
    def _check_memory(self) -> Dict[str, Any]:
        """Check memory usage."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            
            # Determine status based on memory usage
            if memory.percent > 90:
                status = "unhealthy"
            elif memory.percent > 75:
                status = "degraded"
            else:
                status = "healthy"
            
            return {
                "status": status,
                "total_gb": memory.total / (1024**3),
                "available_gb": memory.available / (1024**3),
                "used_percent": memory.percent,
                "used_gb": memory.used / (1024**3)
            }
        except ImportError:
            return {
                "status": "degraded",
                "error": "psutil not available for memory monitoring"
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": f"Memory check failed: {e}",
            }
    
    def _check_circuit_breakers(self) -> Dict[str, Any]:
        """Check circuit breaker states."""
        try:
            circuit_states = get_all_circuit_breaker_states()
            
            # Determine overall status
            status = "healthy"
            disconnecting_breakers = []
            
            for name, state in circuit_states.items():
                if state["state"] == "DISCONNECTING":
                    disconnecting_breakers.append(name)
                    status = "degraded"  # System still functional but cleaning up clients
            
            return {
                "status": status,
                "circuit_breakers": circuit_states,
                "disconnecting_breakers": disconnecting_breakers
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "error": f"Circuit breaker check failed: {e}"
            }
    
    def get_last_health_status(self) -> Optional[HealthStatus]:
        """Get the last cached health status."""
        with self._lock:
            return self._last_health_status
    
    def is_healthy(self) -> bool:
        """Check if system is healthy."""
        health_status = self.get_health_status()
        return health_status.status == "healthy"
    
    def is_degraded(self) -> bool:
        """Check if system is degraded."""
        health_status = self.get_health_status()
        return health_status.status == "degraded"
    
    def is_unhealthy(self) -> bool:
        """Check if system is unhealthy."""
        health_status = self.get_health_status()
        return health_status.status == "unhealthy"


# Global health service instance
_health_service: Optional[HealthService] = None


def get_health_service() -> HealthService:
    """Get or create global health service instance."""
    global _health_service
    if _health_service is None:
        _health_service = HealthService()
    return _health_service


def register_stt_health_checks(stt_service):
    """Register STT service specific health checks for per-client pipeline architecture."""
    health_service = get_health_service()
    
    def check_pipeline_service():
        """Check pipeline service health."""
        try:
            if hasattr(stt_service, 'pipeline_manager'):
                # New pipeline-based service
                stats = stt_service.get_stats()
                manager_stats = stats.get("manager_stats", {})
                
                active_pipelines = manager_stats.get("current_active", 0)
                max_clients = stt_service.config.server.max_concurrent_clients
                utilization = (active_pipelines / max_clients * 100) if max_clients > 0 else 0
                
                if utilization >= 95:
                    status = "unhealthy"
                elif utilization >= 80:
                    status = "degraded"
                else:
                    status = "healthy"
                
                return {
                    "status": status,
                    "active_pipelines": active_pipelines,
                    "max_clients": max_clients,
                    "utilization_percent": round(utilization, 1),
                    "total_created": manager_stats.get("total_created", 0),
                    "total_destroyed": manager_stats.get("total_destroyed", 0),
                    "rejections": manager_stats.get("client_limit_rejections", 0)
                }
            else:
                return {"status": "unhealthy", "error": "Pipeline manager not found"}
        except Exception as e:
            return {"status": "unhealthy", "error": f"Pipeline service check failed: {e}"}
    
    def check_pipeline_health():
        """Check individual pipeline health."""
        try:
            if hasattr(stt_service, 'pipeline_manager'):
                pipeline_info = stt_service.pipeline_manager.get_all_pipeline_info()
                
                total_pipelines = len(pipeline_info)
                active_pipelines = len([p for p in pipeline_info if p["state"] == "active"])
                idle_pipelines = len([p for p in pipeline_info if p["state"] == "idle"])
                
                # Check for any problematic pipelines
                error_pipelines = len([p for p in pipeline_info if "error" in str(p.get("state", ""))])
                
                if error_pipelines > 0:
                    status = "degraded"
                elif total_pipelines == 0:
                    status = "healthy"  # No pipelines is normal when no clients
                else:
                    status = "healthy"
                
                return {
                    "status": status,
                    "total_pipelines": total_pipelines,
                    "active_pipelines": active_pipelines,
                    "idle_pipelines": idle_pipelines,
                    "error_pipelines": error_pipelines
                }
            else:
                return {"status": "unhealthy", "error": "Pipeline manager not available"}
        except Exception as e:
            return {"status": "unhealthy", "error": f"Pipeline health check failed: {e}"}
    
    def check_circuit_breaker():
        """Check circuit breaker status."""
        try:
            if hasattr(stt_service, 'get_circuit_breaker_status'):
                cb_status = stt_service.get_circuit_breaker_status()
                
                if "error" in cb_status:
                    return {"status": "unhealthy", "error": cb_status["error"]}
                
                state = cb_status.get("state", "UNKNOWN")
                if state == "DISCONNECTING":
                    status = "degraded"  # Cleaning up problematic client
                else:
                    status = "healthy"
                
                return {
                    "status": status,
                    "circuit_breaker_state": state,
                    "failure_count": cb_status.get("failure_count", 0),
                    "disconnecting": cb_status.get("disconnecting", False)
                }
            else:
                return {"status": "degraded", "error": "Circuit breaker status not available"}
        except Exception as e:
            return {"status": "unhealthy",
                    "error": f"Circuit breaker check failed: {e}"}
    
    # Register pipeline-specific health checks
    health_service.register_health_check("pipeline_service", check_pipeline_service)
    health_service.register_health_check("pipeline_health", check_pipeline_health)
    health_service.register_health_check("stt_circuit_breaker", check_circuit_breaker)
    
    logger.info("Per-client pipeline health checks registered")


# Health API endpoints
@router.get("/health")
async def health_check():
    """Get detailed health status."""
    health_service = get_health_service()
    health_status = health_service.get_health_status()
    
    # Return appropriate HTTP status code based on health
    if health_status.status == "unhealthy":
        raise HTTPException(status_code=503, detail=health_status.__dict__)
    elif health_status.status == "degraded":
        raise HTTPException(status_code=429, detail=health_status.__dict__)
    
    return health_status.__dict__

@router.get("/health/summary")
async def health_summary():
    """Get health summary."""
    health_service = get_health_service()
    health_status = health_service.get_health_status()
    return {
        "status": health_status.status,
        "timestamp": health_status.timestamp,
        "uptime": health_status.uptime
    }

@router.get("/health/details")
async def health_details():
    """Get detailed health information."""
    health_service = get_health_service()
    health_status = health_service.get_health_status()
    return health_status.__dict__

# Export the router as health_service to match the expected import in main.py
health_service = router