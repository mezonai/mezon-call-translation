"""
Health Monitoring Service for non-realtime STT.
"""
import time
import logging
import threading
from typing import Dict, Any, Optional
from dataclasses import dataclass

logger = logging.getLogger(__name__)


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
    
    def is_healthy(self) -> bool:
        """Check if system is currently healthy."""
        status = self.get_health_status()
        return status.status in ["healthy", "degraded"]
    
    def _check_uptime(self) -> Dict[str, Any]:
        """Check system uptime."""
        uptime_seconds = time.time() - self.start_time
        return {
            "status": "healthy",
            "uptime_seconds": uptime_seconds,
            "uptime_formatted": f"{uptime_seconds:.1f}s"
        }
    
    def _check_memory(self) -> Dict[str, Any]:
        """Check system memory usage."""
        try:
            import psutil
            memory = psutil.virtual_memory()
            
            # Determine status based on memory usage
            if memory.percent > 90:
                status = "unhealthy"
            elif memory.percent > 80:
                status = "degraded"
            else:
                status = "healthy"
                
            return {
                "status": status,
                "total_mb": memory.total / (1024 * 1024),
                "used_mb": memory.used / (1024 * 1024),
                "available_mb": memory.available / (1024 * 1024),
                "percent": memory.percent
            }
        except ImportError:
            return {
                "status": "healthy",
                "message": "psutil not available for memory monitoring"
            }
        except Exception as e:
            logger.error(f"Error checking memory: {e}")
            return {
                "status": "degraded",
                "error": str(e)
            }


# Global health service instance
_health_service: Optional[HealthService] = None


def get_health_service() -> HealthService:
    """Get or create global health service instance."""
    global _health_service
    if _health_service is None:
        _health_service = HealthService()
    return _health_service
