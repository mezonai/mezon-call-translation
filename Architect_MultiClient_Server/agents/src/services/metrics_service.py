import time
from dataclasses import dataclass, field
from typing import Dict, List
from collections import deque
import numpy as np
import threading
from src.logger import get_logger

logger = get_logger(__name__)

@dataclass
class MetricPoint:
    value: float
    timestamp: float = field(default_factory=time.time)

@dataclass
class MetricWindow:
    window_size: int
    values: deque = field(default_factory=lambda: deque(maxlen=1000))
    
    def add(self, value: float):
        self.values.append(MetricPoint(value))
        
    def get_stats(self):
        if not self.values:
            return {
                'min': 0,
                'max': 0,
                'mean': 0,
                'std': 0,
                'p95': 0,
                'count': 0
            }
            
        recent_values = [p.value for p in self.values]
        return {
            'min': float(np.min(recent_values)),
            'max': float(np.max(recent_values)),
            'mean': float(np.mean(recent_values)),
            'std': float(np.std(recent_values)),
            'p95': float(np.percentile(recent_values, 95)),
            'count': len(recent_values)
        }

class MetricsService:
    """Service for collecting and reporting metrics"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(MetricsService, cls).__new__(cls)
                    cls._instance._initialize()
        return cls._instance
    
    def _initialize(self):
        self.metrics: Dict[str, MetricWindow] = {}
        self.labels: Dict[str, Dict[str, str]] = {}
        self.start_time = time.time()
        logger.info("Metrics service initialized")
    
    @classmethod
    def get_instance(cls) -> 'MetricsService':
        return cls()
    
    def track(self, name: str, value: float, labels: Dict[str, str] = None):
        """Track a metric value"""
        if name not in self.metrics:
            self.metrics[name] = MetricWindow(window_size=1000)
        if labels:
            self.labels[name] = labels
            
        self.metrics[name].add(value)
    
    def get_metric(self, name: str) -> dict:
        """Get statistics for a metric"""
        if name not in self.metrics:
            return None
            
        stats = self.metrics[name].get_stats()
        if name in self.labels:
            stats['labels'] = self.labels[name]
        return stats
    
    def get_all_metrics(self) -> Dict[str, dict]:
        """Get all metrics"""
        return {
            name: self.get_metric(name)
            for name in self.metrics
        }
    
    def get_uptime(self) -> float:
        """Get service uptime in seconds"""
        return time.time() - self.start_time
        
    def clear(self):
        """Clear all metrics"""
        self.metrics.clear()
        self.labels.clear()

# Logging cho tracking metrics
def track(self, name: str, value: float, labels: Dict[str, str] = None):
    """Track a metric value"""
    if name not in self.metrics:
        self.metrics[name] = MetricWindow(window_size=1000)
        logger.debug(f"Created new metric window for {name}")
    if labels:
        self.labels[name] = labels
        
    self.metrics[name].add(value)
    logger.debug(f"Tracked metric {name}: {value} (labels: {labels})")
