import threading
import asyncio
import time
from typing import Dict, Set

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
    """
    Pub/Sub manager for SSE connections.
    Each connection has its own asyncio.Queue.
    When a message is pushed, it broadcasts to all connection queues in that room.
    """
    def __init__(self):
        # room_id -> connection_id -> asyncio.Queue
        self.connection_queues: Dict[str, Dict[str, asyncio.Queue]] = {}
        # room_id -> connection_id -> client_id (optional, for duplicate prevention)
        self.connection_clients: Dict[str, Dict[str, str]] = {}
        self._lock = threading.Lock()
        self._connection_counter = 0

    def create_connection_queue(self, room_id: str, connection_id: str) -> asyncio.Queue:
        """Create a dedicated queue for a new connection"""
        with self._lock:
            if room_id not in self.connection_queues:
                self.connection_queues[room_id] = {}
            
            # Create asyncio.Queue for this connection
            conn_queue = asyncio.Queue(maxsize=100)  # Limit to prevent memory issues
            self.connection_queues[room_id][connection_id] = conn_queue
            return conn_queue
    
    def register_connection(self, room_id: str, client_id: str = None) -> str:
        """
        Register a new connection, return connection_id.
        
        Args:
            room_id: Room identifier
            client_id: Optional client identifier for duplicate prevention
        
        Returns:
            Unique connection_id
        """
        with self._lock:
            self._connection_counter += 1
            connection_id = f"{room_id}_{self._connection_counter}_{int(time.time())}"
            
            # Track client_id if provided (for duplicate prevention)
            if client_id:
                if room_id not in self.connection_clients:
                    self.connection_clients[room_id] = {}
                self.connection_clients[room_id][connection_id] = client_id
            
            return connection_id
    
    def disconnect_existing_client(self, room_id: str, client_id: str) -> bool:
        """
        Disconnect existing connection from the same client_id.
        Used to prevent duplicate connections from same client.
        
        Args:
            room_id: Room identifier
            client_id: Client identifier
        
        Returns:
            True if found and disconnected existing connection
        """
        with self._lock:
            if room_id not in self.connection_clients:
                return False
            
            # Find existing connection from same client
            existing_connection_id = None
            for conn_id, cid in self.connection_clients[room_id].items():
                if cid == client_id:
                    existing_connection_id = conn_id
                    break
            
            if existing_connection_id:
                # Remove from tracking
                self.connection_clients[room_id].pop(existing_connection_id, None)
                
                # Remove queue (this will cause generator to stop)
                if room_id in self.connection_queues:
                    self.connection_queues[room_id].pop(existing_connection_id, None)
                
                return True
            
            return False
    
    def unregister_connection(self, room_id: str, connection_id: str):
        """Unregister connection when client disconnects"""
        with self._lock:
            # Remove client tracking
            if room_id in self.connection_clients:
                self.connection_clients[room_id].pop(connection_id, None)
                if not self.connection_clients[room_id]:
                    del self.connection_clients[room_id]
            
            # Remove connection queue
            if room_id in self.connection_queues:
                self.connection_queues[room_id].pop(connection_id, None)
                
                # If no more connections, delete room entry to avoid memory leak
                if not self.connection_queues[room_id]:
                    del self.connection_queues[room_id]
    
    async def broadcast_message(self, room_id: str, message: dict):
        """
        Broadcast message to all connections in a room (Pub/Sub pattern).
        Each connection has its own queue, so all subscribers receive the message.
        """
        with self._lock:
            if room_id not in self.connection_queues:
                return 0  # No active connections
            
            # Get all connection queues for this room
            queues = list(self.connection_queues[room_id].values())
        
        # Broadcast to all queues (outside lock to avoid blocking)
        broadcast_count = 0
        for q in queues:
            try:
                # Non-blocking put - skip if queue is full (slow consumer)
                q.put_nowait(message)
                broadcast_count += 1
            except asyncio.QueueFull:
                # Skip slow consumers to prevent blocking others
                pass
        
        return broadcast_count
    
    def has_active_connections(self, room_id: str) -> bool:
        """Check if room has active connections"""
        with self._lock:
            return room_id in self.connection_queues and len(self.connection_queues[room_id]) > 0
    
    def get_connection_count(self, room_id: str) -> int:
        """Get number of active connections for room"""
        with self._lock:
            return len(self.connection_queues.get(room_id, {}))


