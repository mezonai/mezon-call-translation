"""
Orchestrator Service Client - Handles communication with orchestrator API

Architecture:
- OrchestratorClient: Singleton class with shared HTTP client
- Connection Pooling: Reuses HTTP connections (max 10 connections, 5 keepalive)
- Thread-Safe: Uses asyncio.Lock for singleton initialization
- Auto-Reconnect: HTTP client reinitializes if closed


"""
import asyncio
import httpx
from typing import Optional
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