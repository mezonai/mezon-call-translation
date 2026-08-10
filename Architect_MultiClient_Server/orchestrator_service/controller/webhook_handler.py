from typing import Any

from orchestrator_service.models.webhook_models import TrackInfo, WebhookResponse
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.participant_identity import parse_participant_identity

logger = get_logger(__name__)


class WebhookHandler:
    """LiveKit webhook event handler.

    Recording/egress events no longer flow through here (audio-ingestion
    PLAN.md D2/D18) -- record-service reports directly to
    POST /api/v2/recordings/events (recording_event_service.py). This
    handler is left with the generic room/participant/track lifecycle
    events LiveKit's core SFU still emits regardless of how recording is
    done.
    """

    def __init__(self, transcription_service: TranscriptionService):
        self.transcription_service = transcription_service
        self.room_registry = get_room_registry()

    async def handle_event(self, event: dict[str, Any]) -> WebhookResponse:  # type: ignore[explicit-any]
        """
        Route event to appropriate handler

        Args:
            event: Webhook event data

        Returns:
            WebhookResponse with status and action
        """
        event_type = event.get("event", "unknown")
        logger.info(f"📥 Received: {event_type}")

        room_name = event.get("room", {}).get("name", "")
        if room_name and not await self.room_registry.is_registered(room_name):
            logger.info(f"  ⏭ Room '{room_name}' not registered, skipping event")
            return WebhookResponse(received=True, action="room_not_registered")

        handlers = {
            "participant_joined": self._handle_participant_joined,
            "track_published": self._handle_track_published,
        }

        handler = handlers.get(event_type)
        if handler:
            return await handler(event)

        logger.info("  (ignored)")
        return WebhookResponse(received=True, action="ignored")

    async def _handle_participant_joined(self, event: dict[str, Any]) -> WebhookResponse:  # type: ignore[explicit-any]
        """Handle when a participant joins - currently just logs the event"""
        room_name = event.get("room", {}).get("name", "unknown")
        identity = event.get("participant", {}).get("identity", "unknown")
        username = event.get("participant", {}).get("name") or event.get("participant", {}).get("metadata")
        room_id = await self.room_registry.get_room_id(room_name)
        if not room_id:
            # Shouldn't happen in practice -- handle_event() already checked
            # is_registered() for this room_name before routing here -- but
            # the registry entry could theoretically disappear in the gap
            # between that check and this call.
            logger.warning(f"  ⚠ No room_id for '{room_name}', skipping participant save")
            return WebhookResponse(received=True, action="room_not_registered")
        await self.transcription_service.save_participant(room_id, identity, username=username)

        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant joined: {identity}")

        return WebhookResponse(received=True, action="participant_joined_logged")

    async def _handle_track_published(self, event: dict[str, Any]) -> WebhookResponse:  # type: ignore[explicit-any]
        """Handle when a track is published -- logging only. Recording itself
        is driven by agents/record-service (D3), not triggered from here."""
        room_name = event.get("room", {}).get("name", "unknown")
        identity = event.get("participant", {}).get("identity", "unknown")
        identity = parse_participant_identity(identity)
        track_data = event.get("track", {})
        track = TrackInfo(
            sid=track_data.get("sid", ""),
            mime_type=track_data.get("mimeType", ""),
            source=track_data.get("source", "UNKNOWN"),
        )

        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant: {identity}")
        logger.info(f"  Track: {track.sid} (mime: {track.mime_type}, source: {track.source})")

        return WebhookResponse(received=True, action=f"{track.track_type.lower()}_track_published_logged")
