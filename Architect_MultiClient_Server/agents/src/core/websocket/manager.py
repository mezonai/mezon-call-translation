import asyncio
import json
import time
from typing import Optional, Dict, Any, Callable
import websockets
from dataclasses import dataclass
from src.utils.error_handling import WebSocketError, ErrorContext, ErrorSeverity
from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from src.utils.thread_safe.queue import ThreadSafeQueue
from src.services.metrics_service import MetricsService
from src.logger import get_logger

logger = get_logger(__name__)

@dataclass
class WebSocketStats:
    """WebSocket connection statistics"""
    connected_at: float = 0
    last_message_at: float = 0
    messages_sent: int = 0
    messages_received: int = 0
    bytes_sent: int = 0
    bytes_received: int = 0
    reconnect_count: int = 0
    errors_count: int = 0

class WebSocketConnection:
    """Managed WebSocket connection with metrics and error handling"""
    
    def __init__(self, 
                 url: str,
                 client_id: str,
                 circuit_breaker: CircuitBreaker,
                 on_message: Optional[Callable[[str], None]] = None):
        self.url = url
        self.client_id = client_id
        self.circuit_breaker = circuit_breaker
        self.on_message = on_message
        self.websocket = None
        self.stats = WebSocketStats()
        self.metrics = MetricsService.get_instance()
        self.is_connected = False
        self._message_queue = ThreadSafeQueue(maxsize=1000)
        self._send_task = None
        self._receive_task = None
    
    async def connect(self) -> bool:
        """Establish WebSocket connection"""
        if not self.circuit_breaker.can_try():
            raise WebSocketError(
                "Circuit breaker preventing connection",
                ErrorContext.create(
                    "WebSocketConnection",
                    "connect",
                    ErrorSeverity.HIGH,
                    {"client_id": self.client_id}
                )
            )
        
        try:
            self.websocket = await websockets.connect(
                self.url,
                ping_interval=30,
                ping_timeout=15,
                close_timeout=10,
                max_size=None,
                max_queue=32
            )
            
            self.is_connected = True
            self.stats.connected_at = time.time()
            self.circuit_breaker.record_success()
            
            # Start message processing tasks
            self._send_task = asyncio.create_task(self._process_send_queue())
            self._receive_task = asyncio.create_task(self._process_messages())
            
            self._update_metrics("connected", 1)
            return True
            
        except Exception as e:
            self.circuit_breaker.record_failure()
            self._update_metrics("connection_errors", 1)
            raise WebSocketError(
                f"Connection error: {str(e)}",
                ErrorContext.create(
                    "WebSocketConnection",
                    "connect",
                    ErrorSeverity.HIGH,
                    {"client_id": self.client_id}
                )
            )
    
    async def disconnect(self):
        """Close WebSocket connection"""
        self.is_connected = False
        
        if self._send_task:
            self._send_task.cancel()
        if self._receive_task:
            self._receive_task.cancel()
        
        if self.websocket:
            await self.websocket.close()
            self.websocket = None
        
        self._update_metrics("disconnected", 1)
    
    async def send(self, message: str, timeout: Optional[float] = None) -> bool:
        """Queue message for sending"""
        if not self.is_connected:
            return False
            
        return await self._message_queue.put(message, timeout=timeout)
    
    async def _process_send_queue(self):
        """Process messages in send queue"""
        while self.is_connected:
            try:
                message = await self._message_queue.get(timeout=0.1)
                if message is None:
                    continue
                
                await self._send_message(message)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._update_metrics("send_errors", 1)
                print(f"Error processing send queue: {e}")
    
    async def _send_message(self, message: str):
        """Send message through WebSocket"""
        if not self.websocket:
            return
            
        try:
            await self.websocket.send(message)
            self.stats.messages_sent += 1
            self.stats.bytes_sent += len(message)
            self.stats.last_message_at = time.time()
            self._update_metrics("messages_sent", 1)
            self._update_metrics("bytes_sent", len(message))
            
        except Exception as e:
            self.circuit_breaker.record_failure()
            self._update_metrics("send_errors", 1)
            raise WebSocketError(
                f"Send error: {str(e)}",
                ErrorContext.create(
                    "WebSocketConnection",
                    "send_message",
                    ErrorSeverity.MEDIUM,
                    {"client_id": self.client_id}
                )
            )
    
    async def _process_messages(self):
        """Process incoming messages"""
        while self.is_connected:
            try:
                if not self.websocket:
                    await asyncio.sleep(0.1)
                    continue
                
                message = await self.websocket.recv()
                self.stats.messages_received += 1
                self.stats.bytes_received += len(message)
                self.stats.last_message_at = time.time()
                
                self._update_metrics("messages_received", 1)
                self._update_metrics("bytes_received", len(message))
                
                if self.on_message:
                    await self.on_message(message)
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.circuit_breaker.record_failure()
                self._update_metrics("receive_errors", 1)
                print(f"Error processing messages: {e}")
    
    def _update_metrics(self, metric: str, value: float):
        """Update connection metrics"""
        self.metrics.track(f"websocket.{self.client_id}.{metric}", value)

class WebSocketManager:
    """Manages multiple WebSocket connections"""
    
    def __init__(self, max_connections: int = 10):
        self.max_connections = max_connections
        self.connections: Dict[str, WebSocketConnection] = {}
        self.circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=5,
            reset_timeout=30.0,
            half_open_timeout=5.0
        ))
        self.metrics = MetricsService.get_instance()
    
    async def create_connection(self, 
                              url: str,
                              client_id: str,
                              on_message: Optional[Callable[[str], None]] = None) -> WebSocketConnection:
        """Create and establish a new WebSocket connection"""
        if len(self.connections) >= self.max_connections:
            raise WebSocketError(
                "Maximum connections reached",
                ErrorContext.create(
                    "WebSocketManager",
                    "create_connection",
                    ErrorSeverity.HIGH,
                    {"max_connections": self.max_connections}
                )
            )
        
        if client_id in self.connections:
            return self.connections[client_id]
        
        connection = WebSocketConnection(
            url=url,
            client_id=client_id,
            circuit_breaker=self.circuit_breaker,
            on_message=on_message
        )
        
        await connection.connect()
        self.connections[client_id] = connection
        self._update_metrics()
        return connection
    
    async def close_connection(self, client_id: str):
        """Close a specific connection"""
        if client_id in self.connections:
            await self.connections[client_id].disconnect()
            del self.connections[client_id]
            self._update_metrics()
    
    async def close_all(self):
        """Close all connections"""
        for client_id in list(self.connections.keys()):
            await self.close_connection(client_id)
    
    def get_connection(self, client_id: str) -> Optional[WebSocketConnection]:
        """Get an existing connection"""
        return self.connections.get(client_id)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for all connections"""
        return {
            client_id: connection.stats
            for client_id, connection in self.connections.items()
        }
    
    def _update_metrics(self):
        """Update manager metrics"""
        self.metrics.track("websocket_manager.total_connections", 
                         len(self.connections))
        self.metrics.track("websocket_manager.available_slots",
                         self.max_connections - len(self.connections))
