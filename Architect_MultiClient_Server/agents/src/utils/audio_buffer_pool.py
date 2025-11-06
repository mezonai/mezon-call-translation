import numpy as np
from typing import List, Optional
import threading
from collections import deque
from src.logger import get_logger
from src.services.metrics_service import MetricsService

logger = get_logger(__name__)

class AudioBuffer:
    """Reusable audio buffer with numpy array"""
    
    def __init__(self, size: int, dtype=np.float32):
        self.array = np.zeros(size, dtype=dtype)
        self.size = size
        self.in_use = False
        self.last_used = 0
    
    def clear(self):
        """Reset buffer contents"""
        self.array.fill(0)

class BufferPool:
    """Pool of reusable audio buffers to minimize memory allocation"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(BufferPool, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.available_buffers: deque[AudioBuffer] = deque()
        self.active_buffers: List[AudioBuffer] = []
        self.metrics = MetricsService.get_instance()
        self.total_created = 0
        self.total_reused = 0
    
    @classmethod
    def get_instance(cls) -> 'BufferPool':
        return cls()
    
    def get_buffer(self, size: int, dtype=np.float32) -> AudioBuffer:
        """Get a buffer from the pool or create new if needed"""
        
        # Try to find existing buffer of right size
        with self._lock:
            for _ in range(len(self.available_buffers)):
                buf = self.available_buffers.popleft()
                if buf.size == size:
                    buf.in_use = True
                    self.active_buffers.append(buf)
                    self.total_reused += 1
                    self._track_metrics()
                    return buf
                self.available_buffers.append(buf)
            
            # Create new buffer if none available
            buf = AudioBuffer(size, dtype)
            buf.in_use = True
            self.active_buffers.append(buf)
            self.total_created += 1
            self._track_metrics()
            return buf
    
    def release_buffer(self, buffer: AudioBuffer):
        """Return buffer to the pool"""
        with self._lock:
            if buffer in self.active_buffers:
                self.active_buffers.remove(buffer)
                buffer.clear()
                buffer.in_use = False
                self.available_buffers.append(buffer)
                self._track_metrics()
    
    def _track_metrics(self):
        """Track buffer pool metrics"""
        self.metrics.track("buffer_pool.total_buffers", 
                         len(self.available_buffers) + len(self.active_buffers))
        self.metrics.track("buffer_pool.available_buffers", 
                         len(self.available_buffers))
        self.metrics.track("buffer_pool.active_buffers",
                         len(self.active_buffers))
        self.metrics.track("buffer_pool.created_buffers",
                         self.total_created)
        self.metrics.track("buffer_pool.reused_buffers", 
                         self.total_reused)
    
    def cleanup(self):
        """Clean up all buffers"""
        with self._lock:
            self.available_buffers.clear()
            self.active_buffers.clear()
            self.total_created = 0
            self.total_reused = 0
            self._track_metrics()
