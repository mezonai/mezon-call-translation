"""
Optimized Result Dispatcher for WebSocket Communication
- Per-client asyncio.Queue for independent message handling
- Dedicated dispatcher task per client to prevent bottlenecks
- Non-blocking queue operations with backpressure handling
- Automatic cleanup on client disconnect
"""

import asyncio
import json
import time
import logging
from typing import Dict, Any, Optional
from fastapi import WebSocket
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Singleton instance
_result_dispatcher = None


@dataclass
class ClientDispatcher:
    """Per-client message dispatcher with dedicated queue and task"""
    client_id: str
    session_id: str
    websocket: WebSocket
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=100))
    messages_sent: int = 0
    errors: int = 0
    last_send_time: float = field(default_factory=time.time)
    _dispatcher_task: Optional[asyncio.Task] = None

    async def start(self):
        """Start the dedicated dispatcher loop for this client"""
        if self._dispatcher_task is None:
            self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
            logger.info(f"🚀 Started dispatcher loop for {self.session_id}:{self.client_id}")

    async def stop(self):
        """Stop the dispatcher and clean up"""
        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            try:
                await self._dispatcher_task
            except asyncio.CancelledError:
                pass
            self._dispatcher_task = None
            logger.info(
                f"🛑 Stopped dispatcher loop for {self.session_id}:{self.client_id} "
                f"(sent: {self.messages_sent}, errors: {self.errors})"
            )

    async def _dispatcher_loop(self):
        """Dedicated message dispatcher loop for this client"""
        while True:
            try:
                # Wait for next message
                message = await self.queue.get()
                start_time = time.time()

                # Send via WebSocket with improved formatting
                try:
                    # Gửi trực tiếp payload mà không wrap trong type/data
                    payload = message["payload"]
                    await self.websocket.send_json(payload)  # Gửi payload trực tiếp
                    logger.debug(f"Sent message to client {self.client_id}: {payload}")
                    send_time = (time.time() - start_time) * 1000

                    if send_time > 100:  # Warn if send takes >100ms
                        logger.warning(
                            f"Slow WebSocket send to {self.session_id}:{self.client_id}: "
                            f"{send_time:.1f}ms"
                        )

                    self.messages_sent += 1
                    self.last_send_time = time.time()

                except Exception as e:
                    self.errors += 1
                    logger.error(
                        f"Error sending to {self.session_id}:{self.client_id}: {str(e)}"
                    )
                    # Don't re-raise - keep dispatcher alive

                finally:
                    self.queue.task_done()

            except asyncio.CancelledError:
                # Clean exit on cancellation
                return

            except Exception as e:
                self.errors += 1
                logger.error(
                    f"Error in dispatcher loop for {self.session_id}:{self.client_id}: {str(e)}"
                )
                # Brief pause on error to prevent tight loop
                await asyncio.sleep(0.1)

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics for this client's dispatcher"""
        now = time.time()
        return {
            "client_id": self.client_id,
            "session_id": self.session_id,
            "queue_size": self.queue.qsize(),
            "messages_sent": self.messages_sent,
            "errors": self.errors,
            "last_send_ago_ms": (now - self.last_send_time) * 1000 if self.last_send_time else None
        }


class OptimizedResultDispatcher:
    """
    Optimized WebSocket message dispatcher with per-client queues.
    
    Features:
    - Dedicated asyncio.Queue per client
    - Non-blocking queue operations
    - Independent dispatcher tasks
    - Automatic cleanup
    """

    def __init__(self, metrics=None):
        self.dispatchers: Dict[str, ClientDispatcher] = {}
        self.metrics = metrics
        logger.info("✅ Optimized result dispatcher initialized")

    async def register_client(self, session_id: str, client_id: str, websocket: WebSocket):
        """Register a new client connection"""
        key = f"{session_id}:{client_id}"
        
        if key in self.dispatchers:
            logger.warning(f"Client dispatcher already exists: {key}")
            await self.unregister_client(session_id, client_id)
        
        # Create dedicated dispatcher
        dispatcher = ClientDispatcher(
            client_id=client_id,
            session_id=session_id,
            websocket=websocket
        )
        
        # Start dispatcher loop
        await dispatcher.start()
        
        self.dispatchers[key] = dispatcher
        logger.info(f"Client {client_id} registered with optimized dispatcher")

    async def unregister_client(self, session_id: str, client_id: str):
        """Clean up client resources on disconnect"""
        key = f"{session_id}:{client_id}"
        dispatcher = self.dispatchers.pop(key, None)
        
        if dispatcher:
            await dispatcher.stop()
            logger.info(f"❌ Unregistered client dispatcher: {key}")

    async def emit_result(
        self,
        session_id: str,
        client_id: str,
        result_type: str,
        payload: Dict[str, Any]
    ):
        """Emit result to specific client via dedicated queue"""
        key = f"{session_id}:{client_id}"
        dispatcher = self.dispatchers.get(key)
        
        if not dispatcher:
            logger.error(f"No dispatcher found for {key}")
            return

        message = {
            "type": result_type,
            "payload": payload
        }

        try:
            # Non-blocking put with short timeout
            await asyncio.wait_for(
                dispatcher.queue.put(message),
                timeout=0.1
            )

        except asyncio.TimeoutError:
            logger.warning(
                f"Queue full for {key}, dropping message "
                f"(queue size: {dispatcher.queue.qsize()})"
            )
            if self.metrics:
                try:
                    self.metrics.dropped_messages.inc()
                except:
                    pass

        except Exception as e:
            logger.error(f"Error queueing message for {key}: {e}")

    async def shutdown(self):
        """Clean shutdown of all dispatchers"""
        tasks = []
        for key, dispatcher in self.dispatchers.items():
            logger.info(f"Shutting down dispatcher: {key}")
            tasks.append(dispatcher.stop())
        
        if tasks:
            await asyncio.gather(*tasks)
        self.dispatchers.clear()
        logger.info("All dispatchers shutdown complete")

    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics for all dispatchers"""
        return {
            "total_dispatchers": len(self.dispatchers),
            "clients": [
                dispatcher.get_stats()
                for dispatcher in self.dispatchers.values()
            ]
        }


def get_result_dispatcher(metrics=None) -> OptimizedResultDispatcher:
    """Get or create the global result dispatcher instance"""
    global _result_dispatcher
    if _result_dispatcher is None:
        _result_dispatcher = OptimizedResultDispatcher(metrics=metrics)
    return _result_dispatcher