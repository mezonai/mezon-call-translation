import threading
import queue
import time

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
        self.queues = {}  # room_id -> Queue
        self.connections = {}  # room_id -> set of connection_ids
        self._lock = threading.Lock()
        self._connection_counter = 0

    def get_queue(self, room_id: str) -> queue.Queue:
        """Lấy queue cho room, tạo mới nếu chưa có"""
        with self._lock:
            if room_id not in self.queues:
                self.queues[room_id] = queue.Queue()
            return self.queues[room_id]
    
    def register_connection(self, room_id: str) -> str:
        """Đăng ký một connection mới, trả về connection_id"""
        with self._lock:
            self._connection_counter += 1
            connection_id = f"{room_id}_{self._connection_counter}_{int(time.time())}"
            
            if room_id not in self.connections:
                self.connections[room_id] = set()
            self.connections[room_id].add(connection_id)
            
            return connection_id
    
    def unregister_connection(self, room_id: str, connection_id: str):
        """Hủy đăng ký connection khi client disconnect"""
        with self._lock:
            if room_id in self.connections:
                self.connections[room_id].discard(connection_id)
                
                # Nếu không còn connection nào, xóa queue để tránh memory leak
                if not self.connections[room_id]:
                    del self.connections[room_id]
                    if room_id in self.queues:
                        # Clear queue trước khi xóa
                        q = self.queues[room_id]
                        while not q.empty():
                            try:
                                q.get_nowait()
                            except queue.Empty:
                                break
                        del self.queues[room_id]
    
    def has_active_connections(self, room_id: str) -> bool:
        """Kiểm tra room có connection active không"""
        with self._lock:
            return room_id in self.connections and len(self.connections[room_id]) > 0
    
    def get_connection_count(self, room_id: str) -> int:
        """Lấy số connection active của room"""
        with self._lock:
            return len(self.connections.get(room_id, set()))


