"""
Common decorators for orchestrator service
"""

import threading
from collections.abc import Callable
from functools import wraps
from typing import ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")


def singleton(cls: Callable[P, T]) -> Callable[P, T]:
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
    instances: dict[Callable[P, T], T] = {}
    lock = threading.Lock()

    @wraps(cls)
    def get_instance(*args: P.args, **kwargs: P.kwargs) -> T:
        """Get or create singleton instance"""
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
            return instances[cls]

    return get_instance
