import threading
import queue
from typing import Optional, Any, List
import time
from dataclasses import dataclass

@dataclass
class QueueMetrics:
    """Metrics for queue usage"""
    total_puts: int = 0
    total_gets: int = 0
    max_size: int = 0
    current_size: int = 0
    last_put_time: float = 0.0
    last_get_time: float = 0.0
    total_wait_time: float = 0.0

class ThreadSafeQueue:
    """Thread-safe queue with metrics and enhanced functionality"""
    
    def __init__(self, maxsize: int = 0):
        self._queue = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self.metrics = QueueMetrics()
    
    def put(self, item: Any, timeout: Optional[float] = None, block: bool = True) -> bool:
        """
        Put an item into the queue.
        Returns True if successful, False if timeout occurred.
        """
        try:
            start_time = time.time()
            self._queue.put(item, block=block, timeout=timeout)
            
            with self._lock:
                self.metrics.total_puts += 1
                self.metrics.current_size = self._queue.qsize()
                if self.metrics.current_size > self.metrics.max_size:
                    self.metrics.max_size = self.metrics.current_size
                self.metrics.last_put_time = time.time()
                if block:
                    self.metrics.total_wait_time += time.time() - start_time
            return True
            
        except queue.Full:
            return False
    
    def get(self, timeout: Optional[float] = None, block: bool = True) -> Optional[Any]:
        """
        Get an item from the queue.
        Returns None if timeout occurred.
        """
        try:
            start_time = time.time()
            item = self._queue.get(block=block, timeout=timeout)
            
            with self._lock:
                self.metrics.total_gets += 1
                self.metrics.current_size = self._queue.qsize()
                self.metrics.last_get_time = time.time()
                if block:
                    self.metrics.total_wait_time += time.time() - start_time
            return item
            
        except queue.Empty:
            return None
    
    def get_many(self, n: int, timeout: Optional[float] = None) -> List[Any]:
        """
        Get up to n items from the queue.
        Returns as many items as available within timeout.
        """
        items = []
        start_time = time.time()
        
        while len(items) < n:
            remaining_timeout = None
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    break
                remaining_timeout = timeout - elapsed
            
            item = self.get(timeout=remaining_timeout, block=len(items) == 0)
            if item is None:
                break
            items.append(item)
        
        return items
    
    def clear(self):
        """Clear all items from the queue"""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        
        with self._lock:
            self.metrics = QueueMetrics()
    
    def qsize(self) -> int:
        """Get current size of queue"""
        return self._queue.qsize()
    
    def is_empty(self) -> bool:
        """Check if queue is empty"""
        return self._queue.empty()
    
    def is_full(self) -> bool:
        """Check if queue is full"""
        return self._queue.full()
    
    def get_metrics(self) -> QueueMetrics:
        """Get queue metrics"""
        with self._lock:
            return self.metrics

class AudioQueue(ThreadSafeQueue):
    """Specialized queue for audio data with batch processing support"""
    
    def put_batch(self, items: List[Any], timeout: Optional[float] = None) -> bool:
        """Put a batch of items into the queue"""
        start_time = time.time()
        
        for item in items:
            remaining_timeout = None
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    return False
                remaining_timeout = timeout - elapsed
            
            if not self.put(item, timeout=remaining_timeout):
                return False
        
        return True
    
    def drain(self, timeout: Optional[float] = None) -> List[Any]:
        """
        Drain all items from the queue.
        Returns all available items within timeout.
        """
        items = []
        start_time = time.time()
        
        while True:
            remaining_timeout = None
            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    break
                remaining_timeout = timeout - elapsed
            
            item = self.get(timeout=remaining_timeout, block=False)
            if item is None:
                break
            items.append(item)
        
        return items
