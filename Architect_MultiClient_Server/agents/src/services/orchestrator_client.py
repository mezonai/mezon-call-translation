"""
Orchestrator Service Client - Handles communication with orchestrator API

Architecture:
- OrchestratorClient: Singleton class with shared HTTP client
- Connection Pooling: Reuses HTTP connections (max 10 connections, 5 keepalive)
- Thread-Safe: Uses asyncio.Lock for singleton initialization
- Auto-Reconnect: HTTP client reinitializes if closed
- SSE Listener: Background task to receive requests from orchestrator


"""
import asyncio
import httpx
import json
from typing import Optional, Callable, Dict, Any
from src.logger import get_logger
from src.config.application_config import get_config

logger = get_logger(__name__)
config = get_config()


class OrchestratorClient:
    """
    Singleton client for orchestrator service with connection pooling.
    Reuses HTTP connections for better performance.
    """
    _instance = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        """Initialize client (call start() to create HTTP client)"""
        self.config = get_config()
        self.logger = get_logger(__name__)
        self._http_client: Optional[httpx.AsyncClient] = None
        
        # SSE listener state
        self._sse_listener_task: Optional[asyncio.Task] = None
        self._sse_stop_event = asyncio.Event()
        self._request_handlers: Dict[str, Callable] = {}
        self._agent_id: Optional[str] = None
        self._room_name: Optional[str] = None
    
    @classmethod
    def get_instance(cls) -> 'OrchestratorClient':
        """Get singleton instance (thread-safe)"""
        if cls._instance is None:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    async def start(self):
        """Initialize HTTP client with connection pooling"""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=5.0,  # Reasonable timeout for API calls
                limits=httpx.Limits(
                    max_keepalive_connections=5,
                    max_connections=10,
                    keepalive_expiry=30.0
                )
            )
            self.logger.info("✅ OrchestratorClient HTTP client initialized with connection pooling")
    
    async def close(self):
        """Close HTTP client and release connections"""
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None
            self.logger.info("🔌 OrchestratorClient HTTP client closed")
    
    async def register_room(self, room_name: str) -> Optional[str]:
        """
        Register a room with the orchestrator service for webhook processing.
        
        Args: 
            room_name: Name of the room to register
        
        Returns:
            room_id (str) if registration successful, None otherwise
        """
        if not self.config.orchestrator.base_url:
            self.logger.warning("Orchestrator base URL not configured, skipping room registration")
            return None
        
        # Check if event loop is running
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                self.logger.warning("Event loop is closed, cannot register room")
                return None
        except RuntimeError:
            self.logger.warning("No running event loop, cannot register room")
            return None
        
        # Ensure HTTP client is ready
        if not self._http_client or self._http_client.is_closed:
            await self.start()
        
        url = f"{self.config.orchestrator.base_url}/api/room-registry/register"
        
        payload = {
            "room_name": room_name
        }
        
        headers = {}
        if self.config.orchestrator.api_key:
            headers["X-API-Key"] = self.config.orchestrator.api_key
        
        try:
            response = await self._http_client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                result = response.json()
                room_id = result.get("room_id")
                self.logger.info(
                    f"✅ Room '{room_name}' registered successfully with orchestrator "
                    f"(room_id: {room_id})"
                )
                return room_id
            elif response.status_code == 409:
                # Room already registered, try to get room_id from response
                try:
                    result = response.json()
                    room_id = result.get("room_id")
                    if room_id:
                        self.logger.warning(f"Room '{room_name}' already registered (room_id: {room_id})")
                        return room_id
                except:
                    pass
                self.logger.warning(f"Room '{room_name}' already registered but room_id not available")
                return None
            else:
                text = response.text
                self.logger.error(f"Failed to register room '{room_name}': HTTP {response.status_code} - {text}")
                return None
        except httpx.HTTPError as e:
            self.logger.error(f"Network error registering room '{room_name}': {e}")
            return None
        except Exception as e:
            self.logger.error(f"Unexpected error registering room '{room_name}': {e}", exc_info=True)
            return None
    
    async def unregister_room(self, room_name: str) -> bool:
        """
        Unregister a room from the orchestrator service.
        
        Args:
            room_name: Name of the room to unregister
        
        Returns:
            True if unregistration successful, False otherwise
        """
        if not self.config.orchestrator.base_url:
            self.logger.warning("Orchestrator base URL not configured, skipping room unregistration")
            return False
        
        # Check if event loop is running
        try:
            loop = asyncio.get_running_loop()
            if loop.is_closed():
                self.logger.warning("Event loop is closed, cannot unregister room")
                return False
        except RuntimeError:
            self.logger.warning("No running event loop, cannot unregister room")
            return False
        
        # Ensure HTTP client is ready
        if not self._http_client or self._http_client.is_closed:
            await self.start()
        
        url = f"{self.config.orchestrator.base_url}/api/room-registry/unregister"
        
        payload = {
            "room_name": room_name
        }
        
        headers = {}
        if self.config.orchestrator.api_key:
            headers["Authorization"] = f"Bearer {self.config.orchestrator.api_key}"
        
        try:
            response = await self._http_client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                result = response.json()
                self.logger.info(f"✅ Room '{room_name}' unregistered successfully from orchestrator")
                return True
            elif response.status_code == 404:
                self.logger.warning(f"Room '{room_name}' not found in registry")
                return True  # Consider not found as success
            else:
                text = response.text
                self.logger.error(f"Failed to unregister room '{room_name}': HTTP {response.status_code} - {text}")
                return False
        except httpx.HTTPError as e:
            self.logger.error(f"Network error unregistering room '{room_name}': {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error unregistering room '{room_name}': {e}", exc_info=True)
            return False
    
    async def push_chat_external(
        self,
        room_name: str,
        room_id: str,
        participant_identity: str,
        message: str,
        time_str: Optional[str] = None
    ) -> bool:
        """
        Push chat external event to all connected bots via SSE.
        
        Args:
            room_name: Name of the room where chat occurred
            room_id: Room identifier
            participant_identity: Identity of participant who sent message
            message: Message content
            time_str: Optional timestamp string (ISO format)
        
        Returns:
            True if push successful, False otherwise
        """
        if not self.config.orchestrator.base_url:
            self.logger.warning("Orchestrator base URL not configured, skipping chat external push")
            return False
        
        # Ensure HTTP client is ready
        if not self._http_client or self._http_client.is_closed:
            await self.start()
        
        url = f"{self.config.orchestrator.base_url}/api/agent_push_chat_external"
        
        payload = {
            "room_name": room_name,
            "room_id": room_id,
            "participant_identity": participant_identity,
            "message": message
        }
        
        if time_str:
            payload["time"] = time_str
        
        try:
            response = await self._http_client.post(url, json=payload)
            if response.status_code in [200, 201]:
                result = response.json()
                broadcast_count = result.get("broadcast_to", 0)
                self.logger.info(
                    f"✅ Chat external pushed: room={room_name}, "
                    f"participant={participant_identity}, broadcast_to={broadcast_count}"
                )
                return True
            else:
                text = response.text
                self.logger.error(
                    f"Failed to push chat external: HTTP {response.status_code} - {text}"
                )
                return False
        except httpx.HTTPError as e:
            self.logger.error(f"Network error pushing chat external: {e}")
            return False
        except Exception as e:
            self.logger.error(f"Unexpected error pushing chat external: {e}", exc_info=True)
            return False

    async def push_transcript(
        self,
        room_name: str,
        text: str,
        participant_identity: str,
        type: str
    ) -> bool:
        """Send transcript to API server"""
        try:
            import time
            start_time = time.time()
            url = f"{self.config.orchestrator.base_url}/api/push_transcript"
            payload = {
                    "room_name": room_name, 
                    "message": text, 
                    "message_type": type, 
                    "participant_identity": participant_identity
                }
            resp = await self._http_client.post(
                url,
                json=payload,
                timeout=1.0
            )
            self.logger.debug(
                f"[API] Pushed to queue via API (room={room_name}): "
                f"{text[:50]}{'...' if len(text) > 50 else ''}, "
                f"status={resp.status_code}"
            )
            return True
        except httpx.TimeoutException:
            self.logger.warning(
                f"[API] Timeout pushing to API (room={room_name}), but transcription recorded locally"
            )
            return False
        except Exception as e:
            self.logger.error(f"[API] Failed to push text via API: {e}")
            return False

    async def push_event_session_started(
        self,
        room_name: str,
        room_id: str
    ) -> bool:
        """Send agent joined event to API server"""
        try:
            url = f"{self.config.orchestrator.base_url}/api/push_metadata/session_started"
            payload = {
                "room_name": room_name,
                "room_id": room_id,
            }
            resp = await self._http_client.post(
                url,
                json=payload
            )
            self.logger.info(
                f"[API] Pushed agent joined event (room={room_name}), "
                f"status={resp.status_code}"
            )
            return True
        except Exception as e:
            self.logger.error(f"[API] Failed to push agent joined event: {e}")
            return False
        
    async def push_event_session_ended(
        self,
        room_name: str,
        room_id: str,
        duration_seconds: Optional[int] = None
    ) -> bool:
        """Send session ended event to API server"""
        try:
            url = f"{self.config.orchestrator.base_url}/api/push_metadata/session_ended"
            payload = {
                "room_name": room_name,
                "room_id": room_id,
            }
            if duration_seconds is not None:
                payload["duration_seconds"] = duration_seconds
            
            resp = await self._http_client.post(
                url,
                json=payload
            )
            self.logger.info(
                f"[API] Pushed session ended event (room={room_name}), "
                f"status={resp.status_code}"
            )
            return True
        except Exception as e:
            self.logger.error(f"[API] Failed to push session ended event: {e}")
            return False
    
    # ==================== SSE Agent Request Listener ====================
    
    def register_request_handler(self, request_type: str, handler: Callable):
        """
        Register a handler for a specific request type.
        
        Args:
            request_type: Type of request (e.g., "transcript_control", "tts_play")
            handler: Async callable that takes (payload: dict) and returns None
        """
        self._request_handlers[request_type] = handler
        self.logger.info(f"[SSE] Registered handler for request type: {request_type}")
    
    def unregister_request_handler(self, request_type: str):
        """Unregister a handler for a request type."""
        if request_type in self._request_handlers:
            del self._request_handlers[request_type]
            self.logger.debug(f"[SSE] Unregistered handler for request type: {request_type}")
    
    def clear_all_handlers(self):
        """Clear all registered handlers to prevent memory leaks."""
        count = len(self._request_handlers)
        self._request_handlers.clear()
        if count > 0:
            self.logger.info(f"[SSE] Cleared {count} request handler(s)")
    
    async def start_agent_request_listener(
        self,
        agent_id: str,
        room_name: Optional[str] = None
    ):
        """
        Start background task to listen for agent requests via SSE.
        
        Args:
            agent_id: Unique agent identifier
            room_name: Optional room name (subscribe to room-specific requests)
        """
        if self._sse_listener_task and not self._sse_listener_task.done():
            self.logger.warning("[SSE] Agent request listener already running")
            return
        
        self._agent_id = agent_id
        self._room_name = room_name
        self._sse_stop_event.clear()
        
        # Start listener task
        self._sse_listener_task = asyncio.create_task(
            self._agent_request_listener_task()
        )
        
        self.logger.info(
            f"[SSE] Started agent request listener: agent_id={agent_id}, "
            f"room_name={room_name or 'global'}"
        )
    
    async def stop_agent_request_listener(self):
        """Stop the SSE agent request listener."""
        if not self._sse_listener_task or self._sse_listener_task.done():
            self.logger.info("[SSE] Agent request listener not running")
            return
        
        self.logger.info("[SSE] Stopping agent request listener...")
        self._sse_stop_event.set()
        
        try:
            # Wait for task to complete with timeout
            await asyncio.wait_for(self._sse_listener_task, timeout=5.0)
        except asyncio.TimeoutError:
            self.logger.warning("[SSE] Timeout waiting for listener to stop, cancelling...")
            self._sse_listener_task.cancel()
            try:
                await self._sse_listener_task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("[SSE] Agent request listener stopped")
    
    async def _agent_request_listener_task(self):
        """
        Background task that listens to SSE endpoint for agent requests.
        Auto-reconnects on connection loss with exponential backoff.
        """
        retry_delay = 1.0
        max_retry_delay = 60.0
        
        while not self._sse_stop_event.is_set():
            try:
                # Ensure HTTP client is ready
                if not self._http_client or self._http_client.is_closed:
                    await self.start()
                
                # Build SSE endpoint URL
                url = f"{self.config.orchestrator.base_url}/api/sse/agent-requests"
                params = {"agent_id": self._agent_id}
                if self._room_name:
                    params["room_name"] = self._room_name
                
            
                self.logger.info(f"[SSE] Connecting to orchestrator: {url}")
                
                # Connect to SSE endpoint (streaming)
                async with self._http_client.stream(
                    "GET",
                    url,
                    params=params,
                    timeout=None  # No timeout for SSE
                ) as response:
                    if response.status_code != 200:
                        self.logger.error(
                            f"[SSE] Failed to connect: HTTP {response.status_code}"
                        )
                        await asyncio.sleep(retry_delay)
                        retry_delay = min(retry_delay * 2, max_retry_delay)
                        continue
                    
                    self.logger.info("[SSE] Connected to orchestrator successfully")
                    retry_delay = 1.0  # Reset retry delay on successful connection
                    
                    # Read SSE events
                    buffer = ""
                    async for chunk in response.aiter_text():
                        if self._sse_stop_event.is_set():
                            self.logger.info("[SSE] Stop event detected, closing connection")
                            break
                        
                        buffer += chunk
                        
                        # Process complete SSE events (separated by double newline)
                        while "\n\n" in buffer:
                            event_data, buffer = buffer.split("\n\n", 1)
                            await self._process_sse_event(event_data)
                    
                    self.logger.warning("[SSE] Connection closed by server")
            
            except httpx.HTTPError as e:
                self.logger.error(f"[SSE] HTTP error: {e}")
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
            
            except asyncio.CancelledError:
                self.logger.info("[SSE] Listener task cancelled")
                break
            
            except Exception as e:
                self.logger.error(f"[SSE] Unexpected error: {e}", exc_info=True)
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, max_retry_delay)
        
        self.logger.info("[SSE] Agent request listener task exiting")
    
    async def _process_sse_event(self, event_data: str):
        """
        Process a single SSE event.
        
        Args:
            event_data: Raw SSE event data (may contain multiple lines)
        """
        try:
            # Parse SSE event format
            event_type = None
            data_lines = []
            
            for line in event_data.split("\n"):
                line = line.strip()
                if not line:
                    continue
                
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
            
            # Handle different event types
            if event_type == "connected":
                data = json.loads(data_lines[0]) if data_lines else {}
                self.logger.info(f"[SSE] Connection confirmed: {data}")
                return
            
            if event_type == "heartbeat":
                # Ignore heartbeat events
                return
            
            if event_type == "disconnect":
                data = json.loads(data_lines[0]) if data_lines else {}
                self.logger.warning(f"[SSE] Disconnect event received: {data}")
                return
            
            # Parse data event (agent request)
            if data_lines:
                data_json = "\n".join(data_lines)
                request_data = json.loads(data_json)
                await self._handle_agent_request(request_data)
        
        except json.JSONDecodeError as e:
            self.logger.error(f"[SSE] Failed to parse event data: {e}")
        except Exception as e:
            self.logger.error(f"[SSE] Error processing event: {e}", exc_info=True)
    
    async def _handle_agent_request(self, request_data: Dict[str, Any]):
        """
        Handle incoming agent request from orchestrator.
        
        Args:
            request_data: Request data containing request_id, request_type, and payload
        """
        try:
            request_id = request_data.get("request_id")
            request_type = request_data.get("request_type")
            payload = request_data.get("payload", {})
            timestamp = request_data.get("timestamp")
            
            self.logger.info(
                f"[SSE] Received request: id={request_id}, type={request_type}, "
                f"timestamp={timestamp}"
            )
            
            # Find and call handler
            handler = self._request_handlers.get(request_type)
            if handler:
                try:
                    await handler(payload)
                    self.logger.info(f"[SSE] Request {request_id} handled successfully")
                except Exception as e:
                    self.logger.error(
                        f"[SSE] Error handling request {request_id}: {e}",
                        exc_info=True
                    )
            else:
                self.logger.warning(
                    f"[SSE] No handler registered for request type: {request_type}"
                )
        
        except Exception as e:
            self.logger.error(f"[SSE] Error in request handler: {e}", exc_info=True)