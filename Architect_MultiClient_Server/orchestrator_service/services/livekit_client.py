"""
Centralized LiveKit API Client Service
Singleton pattern for efficient connection management
"""

from contextlib import asynccontextmanager
from typing import Any, Optional

try:
    from livekit import api
    from livekit.api import twirp_client

    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.json_utils import safe_json_loads_object
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class LiveKitServiceError(Exception):
    """Raised when LiveKit operations fail."""

    pass


class LiveKitClientService:
    """
    Centralized LiveKit API client with singleton pattern.
    Provides efficient connection reuse across the application.
    """

    _instance: Optional["LiveKitClientService"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._client: api.LiveKitAPI | None = None
        self._initialized = True
        logger.info("LiveKitClientService initialized")

    @property
    def is_available(self) -> bool:
        """Check if LiveKit API is available"""
        return LIVEKIT_AVAILABLE

    def _validate_config(self):
        """Validate LiveKit configuration"""
        config = get_config()
        if not config.livekit.api_key or not config.livekit.api_secret:
            raise ValueError("LIVEKIT_API_KEY and LIVEKIT_API_SECRET must be set")
        if not config.livekit.http_url:
            raise ValueError("LIVEKIT_URL must be set")
        return config

    def get_client(self) -> api.LiveKitAPI:
        """
        Get or create LiveKit API client (lazy initialization).

        Returns:
            LiveKitAPI instance

        Raises:
            RuntimeError: If LiveKit API is not available
            ValueError: If configuration is invalid
        """
        if not LIVEKIT_AVAILABLE:
            raise RuntimeError("LiveKit API not available. Please install livekit-api package.")

        if self._client is None:
            config = self._validate_config()
            self._client = api.LiveKitAPI(
                url=config.livekit.http_url, api_key=config.livekit.api_key, api_secret=config.livekit.api_secret
            )
            logger.info(f"LiveKit client created for {config.livekit.http_url}")

        return self._client

    @asynccontextmanager
    async def get_client_context(self):
        """
        Context manager for LiveKit client.
        Use this when you need guaranteed cleanup after operation.

        Note: For most cases, use get_client() directly as it reuses connections.
        This context manager creates a NEW client that will be closed after use.

        Yields:
            Tuple of (LiveKitAPI, agent_name)
        """
        if not LIVEKIT_AVAILABLE:
            raise RuntimeError("LiveKit API not available. Please install livekit-api package.")

        config = self._validate_config()
        client = api.LiveKitAPI(
            url=config.livekit.http_url, api_key=config.livekit.api_key, api_secret=config.livekit.api_secret
        )

        try:
            yield client, config.livekit.agent_name
        finally:
            await client.aclose()

    def get_agent_name(self) -> str:
        """Get configured agent name"""
        config = get_config()
        return config.livekit.agent_name

    async def list_dispatches(self, room_name: str):
        """List all dispatches for a room."""
        client = self.get_client()
        try:
            dispatches = await client.agent_dispatch.list_dispatch(room_name=room_name)
            if not isinstance(dispatches, list):
                raise LiveKitServiceError(f"Unexpected list_dispatch response type: {type(dispatches).__name__}")
            return dispatches
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"LiveKit server error: {e}")
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to list dispatches: {e}")

    async def find_agent_dispatch(self, dispatches, agent_name: str | None = None) -> Any | None:
        """Find dispatch by configured or provided agent name."""
        target_agent_name = agent_name or self.get_agent_name()
        for dispatch in dispatches:
            if dispatch.agent_name == target_agent_name:
                return dispatch
        return None

    async def ensure_dispatch(self, room_name: str) -> dict[str, Any]:
        """
        Ensure a dispatch exists for the given room.
        Creates one if it doesn't exist.
        """
        client = self.get_client()
        agent_name = self.get_agent_name()

        dispatches = await self.list_dispatches(room_name)
        if await self.find_agent_dispatch(dispatches, agent_name):
            return {"status": "exists", "message": "Dispatch already exists"}

        try:
            dispatch = await client.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
            )
            return {"status": "created", "dispatch": dispatch}
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"LiveKit server error: {e}")
            raise LiveKitServiceError(f"Failed to create dispatch: {e}")

    async def cancel_dispatch(self, room_name: str) -> dict[str, Any]:
        """Cancel an existing dispatch for the given room."""
        client = self.get_client()
        agent_name = self.get_agent_name()

        dispatches = await self.list_dispatches(room_name)
        target_dispatch = await self.find_agent_dispatch(dispatches, agent_name)

        if not target_dispatch:
            return {"status": "not_found", "message": f"No active dispatch found for agent '{agent_name}'"}

        try:
            await client.agent_dispatch.delete_dispatch(
                target_dispatch.id,
                target_dispatch.room,
            )
            return {
                "status": "cancelled",
                "message": f"Dispatch for agent '{target_dispatch.agent_name}' has been cancelled.",
                "dispatch": target_dispatch,
            }
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to cancel dispatch: {e}")
            raise LiveKitServiceError(f"Failed to cancel dispatch: {e}")

    async def list_participants(self, room_name: str):
        """List participants in a room."""
        client = self.get_client()
        try:
            response = await client.room.list_participants(api.ListParticipantsRequest(room=room_name))
            return [
                {
                    "identity": p.identity,
                    "name": p.name,
                    "state": api.ParticipantInfo.State.Name(p.state),
                    "joined_at": p.joined_at,
                    "metadata": safe_json_loads_object(p.metadata),
                }
                for p in response.participants
            ]
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to list participants: {e}")
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to list participants: {e}")

    async def get_participant_detail(self, room_name: str, identity: str) -> dict[str, Any] | None:
        """
        Get detailed information for a specific participant in a room.

        Args:
            room_name: Name of the room
            identity: Identity of the participant to get details for

        Returns:
            Dict with complete participant details from ParticipantInfo
        """
        client = self.get_client()
        try:
            response = await client.room.list_participants(api.ListParticipantsRequest(room=room_name))

            # Find the specific participant
            for p in response.participants:
                if p.identity == identity:
                    return {
                        "sid": p.sid,
                        "identity": p.identity,
                        "state": api.ParticipantInfo.State.Name(p.state),
                        "name": p.name,
                        "metadata": safe_json_loads_object(p.metadata),
                        "joined_at": p.joined_at,
                        "joined_at_ms": p.joined_at_ms,
                        "version": p.version,
                        "region": p.region,
                        "is_publisher": p.is_publisher,
                        "kind": api.ParticipantInfo.Kind.Name(p.kind) if p.kind else None,
                        "attributes": dict(p.attributes) if p.attributes else {},
                        "disconnect_reason": p.disconnect_reason if p.disconnect_reason else None,
                        "found": True,
                        "tracks": [
                            {
                                "sid": track.sid,
                                "type": api.TrackType.Name(track.type),
                                "name": track.name,
                                "muted": track.muted,
                                "width": track.width,
                                "height": track.height,
                                "source": api.TrackSource.Name(track.source),
                                "mime_type": track.mime_type,
                            }
                            for track in p.tracks
                        ]
                        if p.tracks
                        else [],
                        "permission": {
                            "can_subscribe": p.permission.can_subscribe if p.permission else False,
                            "can_publish": p.permission.can_publish if p.permission else False,
                            "can_publish_data": p.permission.can_publish_data if p.permission else False,
                            "hidden": p.permission.hidden if p.permission else False,
                            "recorder": p.permission.recorder if p.permission else False,
                        }
                        if p.permission
                        else None,
                    }

            # Participant not found
            return {
                "identity": identity,
                "found": False,
                "message": f"Participant '{identity}' not found in room '{room_name}'",
            }
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to get participant detail: {e}")
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to get participant detail: {e}")

    async def cleanup(self):
        """Cleanup LiveKit client connection"""
        if self._client:
            try:
                await self._client.aclose()
                logger.info("LiveKit client closed")
            except Exception as e:
                logger.error(f"Error closing LiveKit client: {e}")
            finally:
                self._client = None

    async def health_check(self) -> dict:
        """
        Check LiveKit service health and configuration.

        Returns:
            Dict with health status information
        """
        if not LIVEKIT_AVAILABLE:
            return {
                "status": "error",
                "message": "LiveKit API not available",
                "configured": False,
            }

        try:
            config = self._validate_config()
            return {
                "status": "ok",
                "message": "LiveKit client is ready",
                "configured": True,
                "url": config.livekit.http_url,
                "agent_name": config.livekit.agent_name,
                "has_credentials": True,
            }
        except ValueError as e:
            return {
                "status": "error",
                "message": str(e),
                "configured": False,
            }


# Global singleton instance
_livekit_service: LiveKitClientService | None = None


def get_livekit_service() -> LiveKitClientService:
    """
    Get the global LiveKit client service instance.

    Returns:
        LiveKitClientService singleton instance
    """
    global _livekit_service
    if _livekit_service is None:
        _livekit_service = LiveKitClientService()
    return _livekit_service


async def cleanup_livekit_service():
    """Cleanup global LiveKit service"""
    global _livekit_service
    if _livekit_service:
        await _livekit_service.cleanup()
        _livekit_service = None
