from typing import Optional, Dict, Any, TypeVar, Generic
import threading
import time
from dataclasses import dataclass
from src.utils.error_handling import ResourceError, ErrorContext, ErrorSeverity
from src.services.metrics_service import MetricsService

T = TypeVar('T')

@dataclass
class ResourceStats:
    """Statistics for a managed resource"""
    created_at: float
    last_used: float
    use_count: int
    total_use_time: float
    is_in_use: bool

class ManagedResource(Generic[T]):
    """Wrapper for a resource with usage tracking"""
    
    def __init__(self, resource: T):
        self.resource = resource
        self.stats = ResourceStats(
            created_at=time.time(),
            last_used=time.time(),
            use_count=0,
            total_use_time=0.0,
            is_in_use=False
        )
        self._lock = threading.Lock()
        self._last_acquire = 0.0
    
    def acquire(self):
        """Mark resource as in use"""
        with self._lock:
            if self.stats.is_in_use:
                return False
            self.stats.is_in_use = True
            self.stats.use_count += 1
            self._last_acquire = time.time()
            return True
    
    def release(self):
        """Mark resource as available"""
        with self._lock:
            if not self.stats.is_in_use:
                return
            self.stats.is_in_use = False
            self.stats.last_used = time.time()
            self.stats.total_use_time += time.time() - self._last_acquire

class ResourcePool(Generic[T]):
    """Pool of managed resources with monitoring"""
    
    def __init__(self, name: str, max_size: int = 10):
        self.name = name
        self.max_size = max_size
        self._resources: Dict[str, ManagedResource[T]] = {}
        self._lock = threading.Lock()
        self._metrics = MetricsService.get_instance()
    
    def add_resource(self, resource_id: str, resource: T) -> bool:
        """Add a resource to the pool"""
        with self._lock:
            if len(self._resources) >= self.max_size:
                return False
            if resource_id in self._resources:
                return False
            
            self._resources[resource_id] = ManagedResource(resource)
            self._update_metrics()
            return True
    
    def remove_resource(self, resource_id: str) -> bool:
        """Remove a resource from the pool"""
        with self._lock:
            if resource_id not in self._resources:
                return False
            
            resource = self._resources[resource_id]
            if resource.stats.is_in_use:
                return False
            
            del self._resources[resource_id]
            self._update_metrics()
            return True
    
    def acquire_resource(self, resource_id: str) -> Optional[T]:
        """Acquire a specific resource"""
        with self._lock:
            if resource_id not in self._resources:
                return None
            
            resource = self._resources[resource_id]
            if not resource.acquire():
                return None
            
            self._update_metrics()
            return resource.resource
    
    def acquire_any(self) -> tuple[Optional[str], Optional[T]]:
        """Acquire any available resource"""
        with self._lock:
            for resource_id, resource in self._resources.items():
                if resource.acquire():
                    self._update_metrics()
                    return resource_id, resource.resource
            return None, None
    
    def release_resource(self, resource_id: str):
        """Release a resource back to the pool"""
        with self._lock:
            if resource_id not in self._resources:
                return
            
            self._resources[resource_id].release()
            self._update_metrics()
    
    def get_stats(self, resource_id: str) -> Optional[ResourceStats]:
        """Get statistics for a resource"""
        with self._lock:
            if resource_id not in self._resources:
                return None
            return self._resources[resource_id].stats
    
    def _update_metrics(self):
        """Update pool metrics"""
        total_resources = len(self._resources)
        in_use = sum(1 for r in self._resources.values() if r.stats.is_in_use)
        
        self._metrics.track(f"resource_pool.{self.name}.total", total_resources)
        self._metrics.track(f"resource_pool.{self.name}.in_use", in_use)
        self._metrics.track(f"resource_pool.{self.name}.available", 
                          total_resources - in_use)

class AudioBufferPool(ResourcePool[Any]):
    """Specialized pool for audio buffers"""
    
    def __init__(self, max_size: int = 10, buffer_size: int = 1024):
        super().__init__("audio_buffers", max_size)
        self.buffer_size = buffer_size
    
    def create_buffer(self, buffer_id: str) -> bool:
        """Create and add a new buffer"""
        import numpy as np
        buffer = np.zeros(self.buffer_size, dtype=np.float32)
        return self.add_resource(buffer_id, buffer)
    
    def get_or_create_buffer(self, buffer_id: str) -> Any:
        """Get an existing buffer or create a new one"""
        buffer = self.acquire_resource(buffer_id)
        if buffer is None:
            if self.create_buffer(buffer_id):
                return self.acquire_resource(buffer_id)
            raise ResourceError(
                "Failed to create buffer",
                ErrorContext.create(
                    "AudioBufferPool",
                    "get_or_create_buffer",
                    ErrorSeverity.HIGH,
                    {"buffer_id": buffer_id}
                )
            )
        return buffer
