from typing import Any

from orchestrator_service.models.webhook_models import (
    EgressInfo,
    LiveKitWebhookEvent,
    TrackInfo,
    WebhookEgressInfo,
    WebhookResponse,
)
from orchestrator_service.services.egress_service import EgressService
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.utils.asyncio_task_manager import asyncio_create_task_safety
from orchestrator_service.utils.filepath import Filepath
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.participant_identity import parse_participant_identity

logger = get_logger(__name__)


class WebhookHandler:
    """Webhook event handler from LiveKit"""

    def __init__(self, egress_service: EgressService, transcription_service: TranscriptionService):
        self.egress_service = egress_service
        self.transcription_service = transcription_service
        self.room_registry = get_room_registry()

    # TODO: Use `Any` type because `event_payload` input in this function is defined by complex types
    async def handle_event(                                     # type: ignore[explicit-any]
        self,
        event_payload: dict[str, Any] | LiveKitWebhookEvent
    ) -> WebhookResponse:
        """
        Route event to appropriate handler

        Args:
            event: Webhook event data

        Returns:
            WebhookResponse with status and action
        """
        event = LiveKitWebhookEvent(**event_payload) if isinstance(event_payload, dict) else event_payload
        event_type = event.event or "unknown"
        logger.info(f"📥 Received: {event_type}")
        if event_type not in {"egress_ended", "egress_started", "egress_updated", "participant_connection_aborted"}:
            logger.info("  (skipping detailed processing for event type)")
            # Get room name - different events have different structures
            room_name = event.room.name if event.room else ""
            if not room_name and event.egress_info:
                # For egress events, room name is in egressInfo
                room_name = event.egress_info.room_name or ""

            # Check if room is registered
            if room_name and not await self.room_registry.is_registered(room_name):
                logger.info(f"  ⏭ Room '{room_name}' not registered, skipping event")
                return WebhookResponse(received=True, action="room_not_registered")

        handlers = {
            "participant_joined": self._handle_participant_joined,
            "track_published": self._handle_track_published,
            "track_unpublished": self._handle_track_unpublished,
            "egress_started": self._handle_egress_started,
            "egress_ended": self._handle_egress_ended,
            "egress_updated": self._handle_egress_updated,
            "participant_connection_aborted": self._handle_participant_connection_aborted,
        }

        handler = handlers.get(event_type)
        if handler:
            return await handler(event)

        logger.info("  (ignored)")
        return WebhookResponse(received=True, action="ignored")

    async def _handle_participant_joined(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when a participant joins - currently just logs the event"""
        room_name = event.room.name if event.room and event.room.name else "unknown"
        identity = event.participant.identity if event.participant and event.participant.identity else "unknown"

        room_id = await self.room_registry.get_room_id(room_name)
        if room_id:
            await self.transcription_service.save_participant(room_id, identity)

        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant joined: {identity}")

        return WebhookResponse(received=True, action="participant_joined_logged")

    async def _handle_track_published(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when a track is published"""
        room_name = event.room.name if event.room and event.room.name else "unknown"
        raw_identity = event.participant.identity if event.participant else "unknown"
        identity = parse_participant_identity(str(raw_identity))

        track = TrackInfo(
            sid=event.track.sid if event.track and event.track.sid else "",
            mime_type=event.track.mime_type if event.track and event.track.mime_type else "",
            source=event.track.source if event.track and event.track.source else "",
        )

        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant: {identity}")
        logger.info(f"  Track: {track.sid} (mime: {track.mime_type}, source: {track.source})")

        if track.is_audio:
            asyncio_create_task_safety(
                self.egress_service.start_recording(room_name, track.sid, track.track_type, track.source, identity)
            )
            return WebhookResponse(received=True, action="recording_started")

        logger.info(f"  ⏭ Skipping {track.track_type}")
        return WebhookResponse(received=True, action=f"skipped_{track.track_type.lower()}")

    async def _handle_track_unpublished(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when a track is unpublished"""
        room_name = event.room.name if event.room and event.room.name else ""
        track_sid = event.track.sid if event.track and event.track.sid else ""

        if await self.egress_service.mark_unpublished(room_name, track_sid):
            logger.info(f"  Track {track_sid} unpublished in room {room_name}, egress auto-stop")
            return WebhookResponse(received=True, action="egress_removed")

        return WebhookResponse(received=True, action="no_active_egress")

    async def _handle_egress_started(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when an egress starts - create track metadata"""
        egress_info = event.egress_info
        if not egress_info:
            return WebhookResponse(received=True, action="ignored_no_egress_info")

        egress_id = egress_info.egress_id or "unknown"
        room_name = egress_info.room_name or "unknown"

        # Get trackId from nested track object
        track_data = egress_info.track
        track_id = track_data.get("trackId") if isinstance(track_data, dict) else None

        # Parse participant identity from filepath
        # Format: "room_name/identity__source-type-timestamp.ext"
        file_data = track_data.get("file") if isinstance(track_data, dict) else None
        filepath = file_data.get("filepath") if isinstance(file_data, dict) else None
        participant_identity: str = "unknown"

        if filepath:
            try:
                parsed = Filepath.parse(str(filepath))
                participant_identity = parsed.get("identity", "")
                logger.debug(f"Parsed participant identity '{participant_identity}' from filepath: {filepath}")
            except ValueError as e:
                logger.warning(f"Failed to parse filepath '{filepath}': {e}")
            except Exception as e:
                logger.warning(f"Unexpected error parsing filepath '{filepath}': {e}")

        logger.info(f"  Egress started: {egress_id} for room {room_name}")
        logger.info(f"  Track: {track_id}, Participant: {participant_identity}")
        logger.info(f"  Filepath: {filepath}")

        # Get or create room to obtain room_ref_id
        try:
            room_ref_id = await self.room_registry.get_room_id(room_name)

            # Save track metadata (filename will be updated later when egress ends)
            asyncio_create_task_safety(
                self.transcription_service.save_track_metadata(
                    egress_id=egress_id,
                    track_id=str(track_id),
                    room_ref_id=str(room_ref_id),
                    participant_identity=participant_identity,
                )
            )

            return WebhookResponse(received=True, action="track_metadata_saved")

        except Exception as e:
            logger.error(f"Error saving track metadata: {e}")
            return WebhookResponse(received=True, action="egress_started_logged")

    async def _handle_egress_updated(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when an egress is updated"""
        egress_info = event.egress_info
        if not egress_info:
            return WebhookResponse(received=True, action="ignored_no_egress_info")

        egress_id = egress_info.egress_id or "unknown"
        status = egress_info.status or "unknown"
        room_name = egress_info.room_name or "unknown"

        logger.info(f"egress_updated: room={room_name}, egress_id={egress_id}, status={status}")
        return WebhookResponse(received=True, action="egress_updated_logged")

    async def _handle_egress_ended(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when an egress ends"""
        egress = event.egress_info
        if not egress:
            return WebhookResponse(received=True, action="ignored_no_egress_info")

        status = egress.status or "unknown"
        room_name = egress.room_name or "unknown"
        egress_id = egress.egress_id or "unknown"

        logger.info(f"  Egress ended: {egress_id} for room {room_name} with status {status}")
        if status != "EGRESS_COMPLETE":
            if status in ["EGRESS_FAILED", "EGRESS_ABORTED"]:
                error = egress.error or "no error info"
                logger.error(f"Egress failed: {error}")

                asyncio_create_task_safety(self._attempt_egress_recovery(room_name, egress_id, error))

                return WebhookResponse(received=True, action="egress_ended_failed")
            logger.info(f"Egress not completed: {status}, egress_ended full event: {event}")
            return WebhookResponse(received=True, action="egress_ended_not_complete")

        egress_info_obj = self._build_egress_info(egress)
        self._log_egress_info(egress_info_obj)

        # Enqueue for transcription
        asyncio_create_task_safety(self.transcription_service.enqueue(egress_info_obj))

        return WebhookResponse(received=True, action="egress_ending_logged")

    async def _handle_participant_connection_aborted(self, event: LiveKitWebhookEvent) -> WebhookResponse:
        """Handle when a participant connection is aborted — log only."""
        room_name = event.room.name if event.room else "unknown"
        egress_id = event.participant.identity if event.participant else "unknown"  # Actually egress_id
        disconnect_reason = event.participant.disconnect_reason if event.participant else "unknown"

        logger.error(
            f"🔴 Egress connection aborted: egress_id={egress_id}, room={room_name}, reason={disconnect_reason}"
        )

        return WebhookResponse(received=True, action="participant_connection_aborted_logged")

    async def _attempt_egress_recovery(self, room_name: str, egress_id: str, error: str) -> None:
        """
        Attempt to restart recording after egress failure.
        Checks if participant is still in room, then restarts egress for the track.
        """
        try:
            pg_repo = self.transcription_service.pg_repo
            if not pg_repo.connected:
                await pg_repo.connect()

            track = await pg_repo.get_track_by_id(track_id=egress_id)
            if not track:
                logger.error(f"[Recovery] No track found in DB for egress: {egress_id}")
                return

            participant_identity = track.participant_identity
            track_id = track.track_id

            logger.info(
                f"[Recovery] Found track: egress={egress_id}, track_id={track_id}, participant={participant_identity}"
            )

            if not participant_identity:
                logger.error(f"[Recovery] No participant_identity in track record for egress: {egress_id}")
                return

            livekit_service = get_livekit_service()
            if not livekit_service.is_available:
                logger.error("[Recovery] LiveKit service not available")
                return

            participant_detail = await livekit_service.get_participant_detail(
                room_name=room_name, identity=participant_identity
            )

            if not participant_detail or not participant_detail.found:
                logger.error(
                    f"[Recovery] Participant '{participant_identity}' not found in room '{room_name}', "
                    f"skipping recovery"
                )
                await pg_repo.save_track_metadata(
                    egress_id=egress_id,
                    error=error,
                    status="failed",
                )
                return

            logger.info(
                f"[Recovery] Participant still in room: identity={participant_detail.identity}, "
                f"state={participant_detail.state}"
            )

            tracks = participant_detail.tracks
            track_found = False
            track_info = None
            if track_id and tracks:
                for t in tracks:
                    if t.sid == track_id:
                        track_found = True
                        track_info = t
                        break

            if track_found and track_info:
                logger.info(
                    f"[Recovery] Track found, restarting recording: "
                    f"sid={track_info.sid}, type={track_info.type}, "
                    f"source={track_info.source}"
                )

                raw_identity = participant_detail.identity
                safe_identity = raw_identity if raw_identity is not None else "unknown"

                asyncio_create_task_safety(
                    self.egress_service.start_recording(
                        room_name=room_name,
                        track_sid=track_info.sid,
                        track_type=track_info.type,
                        source=track_info.source,
                        identity=safe_identity,
                        force_update=True,
                    )
                )
            else:
                logger.error(
                    f"[Recovery] Track {track_id} not found in participant's tracks. "
                    f"Available: {[t.sid for t in tracks]}"
                )
                await pg_repo.save_track_metadata(
                    egress_id=egress_id,
                    error=error,
                    status="failed",
                )

        except Exception as e:
            logger.error(f"[Recovery] Error during egress recovery for {egress_id}: {e}")

    def _build_egress_info(self, egress: WebhookEgressInfo) -> EgressInfo:
        """Build EgressInfo object from event data (simplified)"""
        filepath = ""
        location = ""
        duration: int | str = 0
        started_at: int | str = ""
        ended_at: int | str = ""

        if egress.file:
            filepath = egress.file.filename or egress.file.filepath or ""
            location = egress.file.location or ""
            duration = egress.file.duration or 0
            started_at = egress.file.started_at or ""
            ended_at = egress.file.ended_at or ""

        parsed = Filepath.parse(filepath)
        return EgressInfo(
            egress_id=str(egress.egress_id),
            filename=filepath,
            source=parsed.get("source", ""),
            location=location,
            duration=str(duration),
            started_at=str(started_at),
            ended_at=str(ended_at),
        )

    def _log_egress_info(self, info: EgressInfo):
        """Log egress information"""
        logger.info(f"  Egress: {info.egress_id}")
        logger.info(f"  File: {info.filename}")
        logger.info(f"  Location: {info.location}")
        logger.info(f"  Duration: {info.duration}ns")
        logger.info(f"  Started At: {info.started_at}")
        logger.info(f"  Ended At: {info.ended_at}")
