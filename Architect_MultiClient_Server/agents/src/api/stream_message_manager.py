import threading
import queue

def singleton(cls):
    instances = {}
    lock = threading.Lock()
    def get_instance(*args, **kwargs):
        with lock:
            if cls not in instances:
                instances[cls] = cls(*args, **kwargs)
            return instances[cls]
    return get_instance

@singleton
class StreamMessageManager:
    def __init__(self):
        self.queues = {}
        self._lock = threading.Lock()

    def get_queue(self, room_id):
        with self._lock:
            if room_id not in self.queues:
                self.queues[room_id] = queue.Queue()
            return self.queues[room_id]


