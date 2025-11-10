"""
Base WebSocket Client - Common functionality for all WebSocket connections

This module provides a base class for WebSocket clients with common features:
- Auto-reconnect with exponential backoff
- Circuit breaker integration
- Metrics tracking
- Error handling
- Connection lifecycle management

All specific WebSocket clients (STT, TTS) should inherit from this base class
and implement the abstract `on_message()` method.
"""
from abc import ABC, abstractmethod
import asyncio
import time
from typing import Optional, Callable, Awaitable
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from src.utils.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from src.services.metrics_service import MetricsService
from src.logger import get_logger


class BaseWebSocketClient(ABC):
    """
    Base WebSocket client with common functionality
    
    This abstract base class provides:
    - Connection management with retry logic
    - Auto-reconnect with exponential backoff
    - Circuit breaker pattern for failure handling
    - Metrics tracking (messages, bytes, errors)
    - Graceful connection lifecycle
    
    Subclasses must implement:
    - on_message(): Handle incoming messages
    """
    
    def __init__(
        self,
        url: str,
        client_id: str,
        max_retries: int = 3,
        retry_delay: float = 2.0,
        ping_interval: float = 20.0,
        ping_timeout: float = 10.0
    ):
        """
        Initialize base WebSocket client
        
        Args:
            url: WebSocket server URL (e.g., ws://localhost:8765/ws)
            client_id: Unique client identifier
            max_retries: Maximum connection retry attempts
            retry_delay: Base delay between retries (exponential backoff)
            ping_interval: Interval for ping frames (seconds)
            ping_timeout: Timeout for ping response (seconds)
        """
        self.url = url
        self.client_id = client_id
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.ping_interval = ping_interval
        self.ping_timeout = ping_timeout
        
        # Setup logging and metrics
        self.logger = get_logger(f"websocket.{client_id}")
        self.metrics = MetricsService.get_instance()
        
        # Setup circuit breaker
        self.circuit_breaker = CircuitBreaker(CircuitBreakerConfig(
            failure_threshold=3,
            reset_timeout=30.0,
            half_open_timeout=5.0
        ))
        
        # Connection state
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.is_running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._disconnected_event = asyncio.Event()
        
        # Statistics
        self._stats = {
            "connected_at": 0,
            "messages_sent": 0,
            "messages_received": 0,
            "bytes_sent": 0,
            "bytes_received": 0,
            "reconnect_count": 0,
            "errors": 0
        }
        
        self.logger.debug(f"BaseWebSocketClient initialized for {client_id}")
    
    async def connect(self) -> bool:
        """
        Establish WebSocket connection with retry logic
        
        Returns:
            True if connection successful, False otherwise
        """
        for attempt in range(1, self.max_retries + 1):
            # Check circuit breaker
            if not self.circuit_breaker.can_try():
                self.logger.warning(
                    f"Circuit breaker preventing connection attempt for {self.client_id}"
                )
                return False
            
            try:
                self.logger.info(
                    f"Connecting to {self.url} "
                    f"(attempt {attempt}/{self.max_retries})..."
                )
                
                # Establish WebSocket connection
                self.websocket = await websockets.connect(
                    self.url,
                    ping_interval=self.ping_interval,
                    ping_timeout=self.ping_timeout,
                    close_timeout=10.0,
                    max_size=None,
                    max_queue=32
                )
                
                # Update state
                self.is_connected = True
                self.is_running = True
                self._disconnected_event.clear()
                self._stats["connected_at"] = time.time()
                
                # Record success in circuit breaker
                self.circuit_breaker.record_success()
                
                # Start message receiving loop
                self._receive_task = asyncio.create_task(self._receive_loop())
                
                # Track metrics
                self._track_metric("connected", 1)
                
                self.logger.info(f"✅ Connected to {self.url}")
                return True
                
            except Exception as e:
                self.logger.warning(
                    f"Connection attempt {attempt}/{self.max_retries} failed: {e}"
                )
                
                # Record failure in circuit breaker
                if self.circuit_breaker.record_failure():
                    self.logger.warning(
                        f"Circuit breaker opened for {self.client_id}"
                    )
                
                # Track metrics
                self._track_metric("connection_errors", 1)
                self._stats["errors"] += 1
                
                # Exponential backoff
                if attempt < self.max_retries:
                    delay = self.retry_delay * (2 ** (attempt - 1))
                    delay = min(delay, 30.0)  # Cap at 30 seconds
                    self.logger.info(f"Retrying in {delay:.1f}s...")
                    await asyncio.sleep(delay)
        
        # All retries failed
        self.logger.error(
            f"Failed to connect after {self.max_retries} attempts"
        )
        return False
    
    async def _receive_loop(self):
        """
        Receive and process messages from WebSocket
        
        This loop runs continuously while connected, receiving messages
        and dispatching them to the abstract on_message() handler.
        """
        self.logger.debug(f"Started receiving messages for {self.client_id}")
        
        try:
            async for message in self.websocket:
                # Check if still running
                if not self.is_running:
                    self.logger.info("Receive loop stopped by flag")
                    break
                
                try:
                    # Update stats
                    self._stats["messages_received"] += 1
                    if isinstance(message, bytes):
                        self._stats["bytes_received"] += len(message)
                    elif isinstance(message, str):
                        self._stats["bytes_received"] += len(message.encode())
                    
                    # Track metrics
                    self._track_metric("messages_received", 1)
                    
                    # Dispatch to subclass handler
                    await self.on_message(message)
                    
                except Exception as e:
                    self.logger.error(
                        f"Error processing message: {e}",
                        exc_info=True
                    )
                    self._stats["errors"] += 1
                    self._track_metric("message_errors", 1)
        
        except ConnectionClosed as e:
            self.logger.warning(f"WebSocket connection closed: {e}")
        except WebSocketException as e:
            self.logger.error(f"WebSocket error: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Unexpected error in receive loop: {e}", exc_info=True)
        finally:
            self.is_connected = False
            self._disconnected_event.set()
            self.logger.debug(f"Stopped receiving messages for {self.client_id}")
    
    @abstractmethod
    async def on_message(self, message):
        """
        Handle received message (must be implemented by subclasses)
        
        Args:
            message: Message received from WebSocket (str or bytes)
            
        Note:
            This method is called for each message received from the server.
            Subclasses should implement their specific message handling logic.
        """
        pass
    
    async def send(self, data, max_retries: int = 2) -> bool:
        """
        Send data through WebSocket with retry logic
        
        Args:
            data: Data to send (str or bytes)
            max_retries: Maximum retry attempts for failed sends
            
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.is_connected or not self.websocket:
            self.logger.warning("Cannot send: WebSocket not connected")
            return False
        
        for attempt in range(max_retries):
            try:
                # Send data
                await self.websocket.send(data)
                
                # Update stats
                self._stats["messages_sent"] += 1
                if isinstance(data, bytes):
                    self._stats["bytes_sent"] += len(data)
                elif isinstance(data, str):
                    self._stats["bytes_sent"] += len(data.encode())
                
                # Track metrics
                self._track_metric("messages_sent", 1)
                self._track_metric("bytes_sent", len(data))
                
                return True
                
            except Exception as e:
                self.logger.warning(
                    f"Send failed (attempt {attempt + 1}/{max_retries}): {e}"
                )
                self._stats["errors"] += 1
                self._track_metric("send_errors", 1)
                
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.5)
        
        return False
    
    async def reconnect(self, max_attempts: Optional[int] = None) -> bool:
        """
        Reconnect to WebSocket server
        
        Args:
            max_attempts: Override default max_retries (optional)
            
        Returns:
            True if reconnected successfully, False otherwise
        """
        if not self.circuit_breaker.can_try():
            self.logger.warning("Circuit breaker preventing reconnect")
            return False
        
        self.logger.info(f"Attempting to reconnect {self.client_id}...")
        
        # Clean up existing connection
        await self._cleanup_connection()
        
        # Update stats
        self._stats["reconnect_count"] += 1
        
        # Try to connect with specified attempts
        original_max_retries = self.max_retries
        if max_attempts is not None:
            self.max_retries = max_attempts
        
        try:
            success = await self.connect()
            if success:
                self.logger.info(f"✅ Reconnected successfully")
            return success
        finally:
            self.max_retries = original_max_retries
    
    async def _cleanup_connection(self):
        """Clean up existing connection resources"""
        try:
            # Cancel receive task
            if self._receive_task and not self._receive_task.done():
                self._receive_task.cancel()
                try:
                    await self._receive_task
                except asyncio.CancelledError:
                    pass
            
            # Close WebSocket
            if self.websocket and not self.websocket.closed:
                await self.websocket.close()
        
        except Exception as e:
            self.logger.debug(f"Error during cleanup: {e}")
        finally:
            self.websocket = None
            self._receive_task = None
            self.is_connected = False
    
    async def disconnect(self):
        """
        Gracefully disconnect from WebSocket server
        
        This method performs a clean shutdown:
        1. Stops the receive loop
        2. Cancels pending tasks
        3. Closes the WebSocket connection
        4. Updates state
        """
        self.logger.info(f"Disconnecting {self.client_id}...")
        
        self.is_running = False
        
        # Clean up connection
        await self._cleanup_connection()
        
        # Track metrics
        self._track_metric("disconnected", 1)
        
        self.logger.info(f"✅ Disconnected {self.client_id}")
    
    async def wait_until_disconnected(self):
        """Wait until WebSocket is disconnected"""
        await self._disconnected_event.wait()
    
    def get_stats(self) -> dict:
        """
        Get connection statistics
        
        Returns:
            Dictionary with connection stats
        """
        uptime = 0
        if self._stats["connected_at"] > 0:
            uptime = time.time() - self._stats["connected_at"]
        
        return {
            **self._stats,
            "uptime_seconds": uptime,
            "is_connected": self.is_connected,
            "client_id": self.client_id,
            "url": self.url
        }
    
    def _track_metric(self, name: str, value: float):
        """
        Track metric with client context
        
        Args:
            name: Metric name
            value: Metric value
        """
        self.metrics.track(f"websocket.{self.client_id}.{name}", value)
    
    def __del__(self):
        """Destructor - ensure cleanup"""
        if self.is_connected and self.websocket:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.disconnect())
            except:
                pass
