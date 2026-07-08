"""
SSE Base Utilities
Provides common functions for creating SSE endpoints"""

import asyncio
import json
from collections.abc import AsyncIterable, Callable

from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.sse_manager import SSEManager, SSEMessage
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


async def event_generator(
    channel_type: str,
    context_key: str,
    connection_id: str,
    connection_queue: asyncio.Queue[SSEMessage],
    manager: SSEManager,
    event_filter: Callable[[SSEMessage], bool] | None = None,
    heartbeat_interval: int = 15,
):
    """
    Generic SSE event generator for any channel type.

    Args:
        channel_type: Type of channel (e.g., "messages", "agent")
        context_key: Context identifier (e.g., "room:my-room", "global")
        connection_id: Unique connection identifier
        connection_queue: asyncio.Queue for this connection
        manager: SSEManager instance
        event_filter: Optional filter function to skip certain events
        heartbeat_interval: Seconds between heartbeat events

    Yields:
        SSE formatted events
        Features:
    - Dedicated queue per connection
    - Near-instant delivery (100ms timeout)
    - Automatic heartbeat to keep connection alive
    - Proper cleanup on disconnect
    - Optional event filtering
    """
    logger.info(f"[SSE] Connection started: {connection_id} (channel={channel_type}, context={context_key})")

    try:
        # Send initial event to confirm connection
        yield f"event: connected\ndata: {json.dumps({'connection_id': connection_id, 'channel': channel_type})}\n\n"

        last_heartbeat = asyncio.get_event_loop().time()

        # ⚠️ CRITICAL: Infinite loop is safe because:
        # 1. Uvicorn cancels this coroutine on shutdown
        # 2. We catch CancelledError below for cleanup
        while True:
            try:
                # Short timeout for near-instant message delivery
                data = await asyncio.wait_for(
                    connection_queue.get(),
                    timeout=0.1,  # 100ms - balance between responsiveness and CPU
                )

                # Check for explicit disconnect message (optional, for graceful notify)
                if data is None or (isinstance(data, dict) and data.get("event") == "disconnect"):
                    if data and isinstance(data, dict):
                        logger.info(f"[SSE] Sending disconnect notification to {connection_id}")
                        yield f"event: disconnect\ndata: {json.dumps(data.get('data', {}))}\n\n"
                    break

                # Log message being sent
                logger.info(f"[SSE] Sending to {connection_id}")

                # Send data event
                yield f"data: {json.dumps(data)}\n\n"

            except TimeoutError:
                # No new message - check if heartbeat needs to be sent
                current_time = asyncio.get_event_loop().time()
                if current_time - last_heartbeat >= heartbeat_interval:
                    yield "event: heartbeat\ndata: ping\n\n"
                    last_heartbeat = current_time
                continue

            except asyncio.CancelledError:
                # Client disconnect
                logger.info(f"[SSE] Connection cancelled: {connection_id}")
                break

    except GeneratorExit:
        logger.info(f"[SSE] Generator exit: {connection_id}")
    finally:
        # Cleanup when connection closes
        await manager.unregister_connection(channel_type, context_key, connection_id)
        logger.info(
            f"[SSE] Connection closed and unregistered: {connection_id} (channel={channel_type}, context={context_key})"
        )


def create_sse_response(
    generator: AsyncIterable[str], additional_headers: dict[str, str] | None = None
) -> StreamingResponse:
    """
    Create a standardized SSE StreamingResponse.

    Args:
        generator: Async generator that yields SSE events
        additional_headers: Optional additional headers

    Returns:
        FastAPI StreamingResponse configured for SSE
    """
    default_headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }

    if additional_headers:
        default_headers.update(additional_headers)

    return StreamingResponse(generator, media_type="text/event-stream", headers=default_headers)
