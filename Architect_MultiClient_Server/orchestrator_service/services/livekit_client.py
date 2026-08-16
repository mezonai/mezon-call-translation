"""
Centralized LiveKit API Client Service
Singleton pattern for efficient connection management
"""

from contextlib import asynccontextmanager
from typing import Any, ClassVar

from google.protobuf.json_format import MessageToDict
from pydantic import BaseModel, Field

try:
    from livekit import api
    from livekit.api import twirp_client
    from livekit.protocol.agent_dispatch import AgentDispatch

    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from orchestrator_service.config.application_config import Config, get_config
from orchestrator_service.utils.json_utils import safe_json_loads_object
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class ParticipantBasicInfo(BaseModel):  # type: ignore[explicit-any]
    identity: str = Field(..., description="Identity of the participant")
    name: str = Field(..., description="Name of the participant")
    state: str = Field(..., description="State of the participant")
    joined_at: int = Field(..., description="Timestamp when participant joined")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] = Field(  # type: ignore[explicit-any]
        ..., description="Metadata of the participant"
    )


class AudioTrackInfo(BaseModel):  # type: ignore[explicit-any]
    """Model represent audio track information"""

    participant_identity: str = Field(..., description="Participant identity")
    filename: str = Field(..., description="Audio track filename")
    started_at_ns: int | str | None = Field(default=None, description="Audio track started at (nanoseconds)")
    ended_at_ns: int | str | None = Field(default=None, description="Audio track ended at (nanoseconds)")


class TrackInfo(BaseModel):  # type: ignore[explicit-any]
    sid: str = Field(..., description="SID of the track")
    type: str = Field(..., description="Type of the track")
    name: str = Field(..., description="Name of the track")
    muted: bool = Field(..., description="Muted state of the track")
    width: int = Field(..., description="Width of the track")
    height: int = Field(..., description="Height of the track")
    source: str = Field(..., description="Source of the track")
    mime_type: str = Field(..., description="MIME type of the track")


class ParticipantPermission(BaseModel):  # type: ignore[explicit-any]
    can_subscribe: bool = Field(..., description="Can subscribe permission")
    can_publish: bool = Field(..., description="Can publish permission")
    can_publish_data: bool = Field(..., description="Can publish data permission")
    hidden: bool = Field(..., description="Hidden permission")
    recorder: bool = Field(..., description="Recorder permission")


class ParticipantDetail(BaseModel):  # type: ignore[explicit-any]
    identity: str = Field(..., description="Identity of the participant")
    found: bool = Field(..., description="Found status")
    message: str | None = Field(default=None, description="Message")
    sid: str | None = Field(default=None, description="SID of the participant")
    state: str | None = Field(default=None, description="State of the participant")
    name: str | None = Field(default=None, description="Name of the participant")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] | None = Field(  # type: ignore[explicit-any]
        default=None, description="Metadata of the participant"
    )

    joined_at: int | None = Field(default=None, description="Timestamp when participant joined")
    joined_at_ms: int | None = Field(default=None, description="Timestamp in milliseconds when participant joined")
    version: int | None = Field(default=None, description="Version of the participant")
    region: str | None = Field(default=None, description="Region of the participant")
    is_publisher: bool | None = Field(default=None, description="Is publisher")
    kind: str | None = Field(default=None, description="Kind of the participant")
    attributes: dict[str, str] | None = Field(default=None, description="Attributes of the participant")
    disconnect_reason: int | None = Field(default=None, description="Reason for disconnection")
    tracks: list[TrackInfo] = Field(default_factory=list, description="Tracks of the participant")
    permission: ParticipantPermission | None = Field(default=None, description="Permission of the participant")


class DispatchActionResponseModel(BaseModel):  # type: ignore[explicit-any]
    status: str = Field(..., description="Status of the operation")
    message: str | None = Field(default=None, description="Message")
    # TODO: Use `Any` type because `dispatch` is converted from `AgentDispatch` protobuf to dict
    dispatch: dict[str, Any] | None = Field(default=None, description="Dispatch information")  # type: ignore[explicit-any]


class ParticipantModel(BaseModel):  # type: ignore[explicit-any]
    identity: str = Field(..., description="Identity of the participant")
    name: str = Field(..., description="Name of the participant")
    state: str = Field(..., description="State of the participant")
    joined_at: int = Field(..., description="Timestamp when participant joined")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] = Field(  # type: ignore[explicit-any]
        ..., description="Metadata of the participant"
    )


class ParticipantListResponseModel(BaseModel):  # type: ignore[explicit-any]
    status: str = Field(..., description="Status of the operation")
    participants: list[ParticipantModel] = Field(default_factory=list, description="List of participants")


class LiveKitServiceError(Exception):
    """Raised when LiveKit operations fail."""

    pass


class LiveKitClientService:
    """
    Centralized LiveKit API client with singleton pattern.
    Provides efficient connection reuse across the application.
    """

    _instance: ClassVar["LiveKitClientService | None"] = None
    _initialized: bool

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

    def _validate_config(self) -> Config:
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
                raise LiveKitServiceError(f"LiveKit server error: {e}") from e
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to list dispatches: {e}") from e

    async def find_agent_dispatch(
        self, dispatches: list[AgentDispatch], agent_name: str | None = None
    ) -> AgentDispatch | None:
        """Find dispatch by configured or provided agent name."""
        target_agent_name = agent_name or self.get_agent_name()
        for dispatch in dispatches:
            if dispatch.agent_name == target_agent_name:
                return dispatch
        return None

    async def ensure_dispatch(self, room_name: str) -> DispatchActionResponseModel:
        """
        Ensure a dispatch exists for the given room.
        Creates one if it doesn't exist.
        """
        client = self.get_client()
        agent_name = self.get_agent_name()

        dispatches = await self.list_dispatches(room_name)
        if await self.find_agent_dispatch(dispatches, agent_name):
            return DispatchActionResponseModel(status="exists", message="Dispatch already exists")

        try:
            dispatch = await client.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(agent_name=agent_name, room=room_name)
            )
            return DispatchActionResponseModel(
                status="created",
                dispatch=MessageToDict(dispatch, preserving_proto_field_name=True),  # type: ignore[explicit-any]
            )
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"LiveKit server error: {e}") from e
            raise LiveKitServiceError(f"Failed to create dispatch: {e}") from e

    async def cancel_dispatch(self, room_name: str) -> DispatchActionResponseModel:
        """Cancel an existing dispatch for the given room."""
        client = self.get_client()
        agent_name = self.get_agent_name()

        dispatches = await self.list_dispatches(room_name)
        target_dispatch = await self.find_agent_dispatch(dispatches, agent_name)

        if not target_dispatch:
            return DispatchActionResponseModel(
                status="not_found", message=f"No active dispatch found for agent '{agent_name}'"
            )

        try:
            await client.agent_dispatch.delete_dispatch(
                target_dispatch.id,
                target_dispatch.room,
            )
            return DispatchActionResponseModel(
                status="cancelled",
                message=f"Dispatch for agent '{target_dispatch.agent_name}' has been cancelled.",
                dispatch=MessageToDict(target_dispatch, preserving_proto_field_name=True),  # type: ignore[explicit-any]
            )
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to cancel dispatch: {e}") from e
            raise LiveKitServiceError(f"Failed to cancel dispatch: {e}") from e

    async def list_participants(self, room_name: str) -> list[ParticipantBasicInfo]:
        """List participants in a room."""
        client = self.get_client()
        try:
            response = await client.room.list_participants(api.ListParticipantsRequest(room=room_name))
            return [
                ParticipantBasicInfo(
                    identity=p.identity,
                    name=p.name,
                    state=api.ParticipantInfo.State.Name(p.state),
                    joined_at=p.joined_at,
                    metadata=safe_json_loads_object(p.metadata),
                )
                for p in response.participants
            ]
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to list participants: {e}") from e
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to list participants: {e}") from e

    async def get_participant_detail(self, room_name: str, identity: str) -> ParticipantDetail | None:
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
                    tracks = (
                        [
                            TrackInfo(
                                sid=track.sid,
                                type=api.TrackType.Name(track.type),
                                name=track.name,
                                muted=track.muted,
                                width=track.width,
                                height=track.height,
                                source=api.TrackSource.Name(track.source),
                                mime_type=track.mime_type,
                            )
                            for track in p.tracks
                        ]
                        if p.tracks
                        else []
                    )

                    permission = (
                        ParticipantPermission(
                            can_subscribe=p.permission.can_subscribe if p.permission else False,
                            can_publish=p.permission.can_publish if p.permission else False,
                            can_publish_data=p.permission.can_publish_data if p.permission else False,
                            hidden=p.permission.hidden if p.permission else False,
                            recorder=p.permission.recorder if p.permission else False,
                        )
                        if p.permission
                        else None
                    )

                    return ParticipantDetail(
                        found=True,
                        identity=p.identity,
                        sid=p.sid,
                        state=api.ParticipantInfo.State.Name(p.state),
                        name=p.name,
                        metadata=safe_json_loads_object(p.metadata),
                        joined_at=p.joined_at,
                        joined_at_ms=p.joined_at_ms,
                        version=p.version,
                        region=p.region,
                        is_publisher=p.is_publisher,
                        kind=api.ParticipantInfo.Kind.Name(p.kind) if p.kind else None,
                        attributes=dict(p.attributes) if p.attributes else {},
                        disconnect_reason=p.disconnect_reason if p.disconnect_reason else None,
                        tracks=tracks,
                        permission=permission,
                    )

            # Participant not found
            return ParticipantDetail(
                identity=identity,
                found=False,
                message=f"Participant '{identity}' not found in room '{room_name}'",
            )
        except Exception as e:
            if LIVEKIT_AVAILABLE and isinstance(e, twirp_client.TwirpError):
                raise LiveKitServiceError(f"Failed to get participant detail: {e}") from e
            if isinstance(e, LiveKitServiceError):
                raise
            raise LiveKitServiceError(f"Failed to get participant detail: {e}") from e

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

    async def health_check(self) -> dict[str, str | bool]:
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
