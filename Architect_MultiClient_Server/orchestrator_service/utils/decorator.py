"""
Common decorators for orchestrator service
"""
import threading
from functools import wraps


def singleton(cls):
    """
    Singleton decorator to ensure only one instance of a class exists.
    Thread-safe implementation using threading.Lock.
    
    Usage:
        @singleton
        class MyClass:
            def __init__(self):
                pass
    
    Args:
        cls: Class to be converted to singleton
    
    Returns:
        Wrapper function that returns the singleton instance
    """
    instances = {}
    lock = threading.Lock()
    
    @wraps(cls)
    def get_instance(*args, **kwargs):
        """Get or create singleton instance"""
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
            return instances[cls]
    
    return get_instance
