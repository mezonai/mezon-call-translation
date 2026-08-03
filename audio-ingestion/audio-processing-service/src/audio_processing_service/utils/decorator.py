"""Common decorators. Copied from stt_service/utils/decorator.py."""

import threading
from functools import wraps


def singleton(cls):
    """Thread-safe singleton decorator."""
    instances = {}
    lock = threading.Lock()

    @wraps(cls)
    def get_instance(*args, **kwargs):
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
            return instances[cls]

    return get_instance
