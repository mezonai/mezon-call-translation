"""
Thread-Safe Buffer and Audio Chunk Management

Provides thread-safe buffer implementations for concurrent audio processing.
Includes specialized audio buffers with metrics tracking and zero-copy operations
where possible.

Components:
    - BufferMetrics: Dataclass for tracking buffer usage statistics
    - ThreadSafeBuffer: Generic thread-safe FIFO buffer with blocking operations
    - AudioBuffer: Specialized buffer for numpy audio arrays
    - AudioChunk: Dataclass representing an audio chunk with metadata

Thread Safety:
    All operations are protected by threading.Lock to ensure safe concurrent access
    from multiple producer/consumer threads.

Performance:
    - Uses collections.deque for efficient FIFO operations
    - Tracks metrics without significant overhead
    - Supports timeout-based blocking for flow control
"""
import threading
import numpy as np
from typing import Optional, List, Any
from collections import deque
import time
from dataclasses import dataclass

@dataclass
class BufferMetrics:
    """Metrics for buffer usage"""
    total_items: int = 0
    total_bytes: int = 0
    max_items: int = 0
    max_bytes: int = 0
    last_write_time: float = 0.0
    last_read_time: float = 0.0

class ThreadSafeBuffer:
    """Thread-safe circular buffer implementation"""
    
    def __init__(self, maxsize: int = 1000):
        self._buffer = deque(maxlen=maxsize)
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)
        self.maxsize = maxsize
        self.metrics = BufferMetrics()
    
    def put(self, item: Any, timeout: Optional[float] = None) -> bool:
        """
        Put an item into the buffer.
        Returns True if successful, False if timeout occurred.
        """
        with self._lock:
            if self.maxsize > 0:
                while len(self._buffer) >= self.maxsize:
                    if timeout is not None:
                        if not self._not_full.wait(timeout):
                            return False
                    else:
                        self._not_full.wait()
            
            self._buffer.append(item)
            self._update_metrics_put(item)
            self._not_empty.notify()
            return True
    
    def get(self, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Get an item from the buffer.
        Returns None if timeout occurred.
        """
        with self._lock:
            while len(self._buffer) == 0:
                if timeout is not None:
                    if not self._not_empty.wait(timeout):
                        return None
                else:
                    self._not_empty.wait()
            
            item = self._buffer.popleft()
            self._update_metrics_get()
            self._not_full.notify()
            return item
    
    def clear(self):
        """Clear all items from the buffer"""
        with self._lock:
            self._buffer.clear()
            self.metrics = BufferMetrics()
            self._not_full.notify_all()
    
    def is_empty(self) -> bool:
        """Check if buffer is empty"""
        with self._lock:
            return len(self._buffer) == 0
    
    def is_full(self) -> bool:
        """Check if buffer is full"""
        with self._lock:
            return self.maxsize > 0 and len(self._buffer) >= self.maxsize
    
    def qsize(self) -> int:
        """Get current size of buffer"""
        with self._lock:
            return len(self._buffer)
    
    def get_metrics(self) -> BufferMetrics:
        """Get buffer metrics"""
        with self._lock:
            return self.metrics
    
    def _update_metrics_put(self, item: Any):
        """Update metrics after putting an item"""
        self.metrics.total_items += 1
        current_size = len(self._buffer)
        if current_size > self.metrics.max_items:
            self.metrics.max_items = current_size
            
        if isinstance(item, (bytes, np.ndarray)):
            item_size = len(item)
            self.metrics.total_bytes += item_size
            if item_size > self.metrics.max_bytes:
                self.metrics.max_bytes = item_size
        
        self.metrics.last_write_time = time.time()
    
    def _update_metrics_get(self):
        """Update metrics after getting an item"""
        self.metrics.last_read_time = time.time()

class AudioBuffer(ThreadSafeBuffer):
    """Specialized buffer for audio data with numpy array support"""
    
    def put_array(self, array: np.ndarray, timeout: Optional[float] = None) -> bool:
        """Put a numpy array into the buffer"""
        if not isinstance(array, np.ndarray):
            raise TypeError("Input must be a numpy array")
        return self.put(array.copy(), timeout)
    
    def get_array(self, timeout: Optional[float] = None) -> Optional[np.ndarray]:
        """Get a numpy array from the buffer"""
        item = self.get(timeout)
        return item if item is not None else None
    
    def get_concatenated(self, n: int, timeout: Optional[float] = None) -> Optional[np.ndarray]:
        """Get n items concatenated into a single array"""
        arrays = []
        start_time = time.time()
        
        while len(arrays) < n:
            remaining_timeout = None
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    return None
                remaining_timeout = timeout - elapsed
            
            array = self.get_array(remaining_timeout)
            if array is None:
                return None
            arrays.append(array)
        
        return np.concatenate(arrays)

class AudioChunk:
    """Container for audio data with metadata"""
    
    def __init__(self, 
                 data: np.ndarray,
                 is_speech: bool = False,
                 timestamp: float = None,
                 chunk_id: int = None):
        self.data = data
        self.is_speech = is_speech
        self.timestamp = timestamp or time.time()
        self.chunk_id = chunk_id
