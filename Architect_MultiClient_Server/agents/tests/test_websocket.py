import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from src.core.websocket.manager import WebSocketManager, WebSocketConnection
from src.utils.circuit_breaker import CircuitBreaker
from src.services.metrics_service import MetricsService

@pytest.fixture
def metrics_service():
    """Fixture for metrics service"""
    service = MetricsService.get_instance()
    service.clear_metrics()
    return service

@pytest.fixture
def circuit_breaker():
    """Fixture for circuit breaker"""
    return CircuitBreaker()

@pytest.fixture
def websocket_manager():
    """Fixture for websocket manager"""
    return WebSocketManager(max_connections=5)

@pytest.mark.asyncio
async def test_websocket_connection_creation(websocket_manager):
    """Test creating new websocket connections"""
    url = "ws://test.com"
    client_id = "test_client"
    
    # Mock websockets.connect
    websocket_manager.circuit_breaker.can_try = MagicMock(return_value=True)
    
    connection = await websocket_manager.create_connection(
        url=url,
        client_id=client_id
    )
    
    assert connection is not None
    assert connection.url == url
    assert connection.client_id == client_id
    assert websocket_manager.get_connection(client_id) == connection

@pytest.mark.asyncio
async def test_websocket_max_connections(websocket_manager):
    """Test max connections limit"""
    with pytest.raises(Exception):
        for i in range(websocket_manager.max_connections + 1):
            await websocket_manager.create_connection(
                url=f"ws://test{i}.com",
                client_id=f"client_{i}"
            )

@pytest.mark.asyncio
async def test_websocket_message_sending(websocket_manager):
    """Test message sending through websocket"""
    client_id = "test_client"
    message = "test_message"
    
    # Setup mock connection
    connection = await websocket_manager.create_connection(
        url="ws://test.com",
        client_id=client_id
    )
    connection._send_message = AsyncMock()
    
    # Send message
    await connection.send(message)
    await asyncio.sleep(0.1)  # Allow message processing
    
    connection._send_message.assert_called_once_with(message)

@pytest.mark.asyncio
async def test_websocket_message_receiving(websocket_manager):
    """Test message receiving through websocket"""
    client_id = "test_client"
    message = "test_message"
    received_messages = []
    
    async def on_message(msg):
        received_messages.append(msg)
    
    # Setup connection with message handler
    connection = await websocket_manager.create_connection(
        url="ws://test.com",
        client_id=client_id,
        on_message=on_message
    )
    
    # Simulate receiving message
    if connection.websocket:
        connection.websocket.recv = AsyncMock(return_value=message)
    
    await asyncio.sleep(0.1)  # Allow message processing
    assert message in received_messages

@pytest.mark.asyncio
async def test_websocket_connection_close(websocket_manager):
    """Test closing websocket connection"""
    client_id = "test_client"
    
    connection = await websocket_manager.create_connection(
        url="ws://test.com",
        client_id=client_id
    )
    
    await websocket_manager.close_connection(client_id)
    assert websocket_manager.get_connection(client_id) is None

@pytest.mark.asyncio
async def test_websocket_metrics(websocket_manager, metrics_service):
    """Test websocket metrics tracking"""
    client_id = "test_client"
    
    connection = await websocket_manager.create_connection(
        url="ws://test.com",
        client_id=client_id
    )
    
    # Check connection metrics
    assert metrics_service.get_latest(
        f"websocket.{client_id}.connected"
    ) is not None
    
    # Send and receive messages
    await connection.send("test")
    await asyncio.sleep(0.1)
    
    # Check message metrics
    assert metrics_service.get_latest(
        f"websocket.{client_id}.messages_sent"
    ) is not None
