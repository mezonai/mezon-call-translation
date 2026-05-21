import asyncio
import json
from datetime import datetime
from typing import Dict, Any
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.egress_service import EgressService
from orchestrator_service.services.transcription_service import TranscriptionService
from orchestrator_service.services.room_registry import get_room_registry
from orchestrator_service.utils.filepath import Filepath
from orchestrator_service.models.webhook_models import (
    WebhookResponse,
    TrackInfo,
    EgressInfo,
)
from orchestrator_service.utils.participant_identity import parse_participant_identity


logger = get_logger(__name__)


class WebhookHandler:
    """Webhook event handler from LiveKit"""

    def __init__(
        self, egress_service: EgressService, transcription_service: TranscriptionService
    ):
        self.egress_service = egress_service
        self.transcription_service = transcription_service
        self.room_registry = get_room_registry()

    async def handle_event(self, event: Dict[str, Any]) -> WebhookResponse:
        """
        Route event to appropriate handler

        Args:
            event: Webhook event data

        Returns:
            WebhookResponse with status and action
        """
        event_type = event.get("event", "unknown")
        logger.info(f"📥 Received: {event_type}")
        if event_type not in {"egress_ended", "egress_started", "egress_updated", "participant_connection_aborted"}:
            logger.info(f"  (skipping detailed processing for event type)")
            # Get room name - different events have different structures
            room_name = event.get("room", {}).get("name", "")
            if not room_name:
                # For egress events, room name is in egressInfo
                room_name = event.get("egressInfo", {}).get("roomName", "")

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

        logger.info(f"  (ignored)")
        return WebhookResponse(received=True, action="ignored")

    async def _handle_participant_joined(self, event: Dict) -> WebhookResponse:
        """Handle when a participant joins - currently just logs the event"""
        room_name = event.get("room", {}).get("name", "unknown")
        identity = event.get("participant", {}).get("identity", "unknown")
        room_id = await self.room_registry.get_room_id(room_name)
        await self.transcription_service.save_participant(room_id, identity)

        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant joined: {identity}")

        return WebhookResponse(received=True, action="participant_joined_logged")

    async def _handle_track_published(self, event: Dict) -> WebhookResponse:
        """Handle when a track is published"""
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
        logger.info(
            f"  Track: {track.sid} (mime: {track.mime_type}, source: {track.source})"
        )

        if track.is_audio:
            asyncio.create_task(
                self.egress_service.start_recording(
                    room_name, track.sid, track.track_type, track.source, identity
                )
            )
            return WebhookResponse(received=True, action="recording_started")

        logger.info(f"  ⏭ Skipping {track.track_type}")
        return WebhookResponse(
            received=True, action=f"skipped_{track.track_type.lower()}"
        )

    async def _handle_track_unpublished(self, event: Dict) -> WebhookResponse:
        """Handle when a track is unpublished"""
        room_name = event.get("room", {}).get("name", "")
        track_sid = event.get("track", {}).get("sid", "")

        if await self.egress_service.mark_unpublished(room_name, track_sid):
            logger.info(
                f"  Track {track_sid} unpublished in room {room_name}, egress auto-stop"
            )
            return WebhookResponse(received=True, action="egress_removed")

        return WebhookResponse(received=True, action="no_active_egress")

    async def _handle_egress_started(self, event: Dict) -> WebhookResponse:
        """Handle when an egress starts - create track metadata"""
        egress_info = event.get("egressInfo")

        egress_id = egress_info.get("egressId")
        room_name = egress_info.get("roomName")

        # Get trackId from nested track object
        track_data = egress_info.get("track")
        track_id = track_data.get("trackId")

        # Parse participant identity from filepath
        # Format: "room_name/identity__source-type-timestamp.ext"
        filepath = track_data.get("file").get("filepath")
        participant_identity = "unknown"

        if filepath:
            try:
                parsed = Filepath.parse(filepath)
                participant_identity = parsed.get("identity")
                logger.debug(
                    f"Parsed participant identity '{participant_identity}' from filepath: {filepath}"
                )
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
            asyncio.create_task(
                self.transcription_service.save_track_metadata(
                    egress_id=egress_id,
                    track_id=track_id,
                    room_ref_id=room_ref_id,
                    participant_identity=participant_identity,
                )
            )

            return WebhookResponse(received=True, action="track_metadata_saved")

        except Exception as e:
            logger.error(f"Error saving track metadata: {e}")
            return WebhookResponse(received=True, action="egress_started_logged")

    async def _handle_egress_updated(self, event: Dict) -> WebhookResponse:
        """Handle when an egress is updated"""
        egress_info = event.get("egressInfo", {})
        egress_id = egress_info.get("egressId", "unknown")
        status = egress_info.get("status", "unknown")
        room_name = egress_info.get("roomName", "unknown")
        logger.info(
            f"egress_updated: room={room_name}, egress_id={egress_id}, status={status}"
        )
        return WebhookResponse(received=True, action="egress_updated_logged")

    async def _handle_egress_ended(self, event: Dict) -> WebhookResponse:
        """Handle when an egress ends"""
        egress = event.get("egressInfo", {})
        status = egress.get("status", "unknown")

        room_name = egress.get("roomName", "unknown")
        egress_id = egress.get("egressId", "unknown")
        logger.info(
            f"  Egress ended: {egress_id} for room {room_name} with status {status}"
        )
        if status != "EGRESS_COMPLETE":
            if status in ["EGRESS_FAILED", "EGRESS_ABORTED"]:
                error = egress.get("error", "no error info")
                logger.error(f"Egress failed: {error}")
                pg_repo = self.transcription_service.pg_repo
                if not pg_repo.connected:
                    await pg_repo.connect()
                await pg_repo.save_track_metadata(
                    egress_id=egress_id,
                    error=error,
                    status="failed",
                )
                return WebhookResponse(received=True, action="egress_ended_failed")
            logger.info(
                f"Egress not completed: {status}, egress_ended full event: {event}"
            )
            return WebhookResponse(received=True, action="egress_ended_not_complete")

        file_data = egress.get("file", {})

        egress_info = self._build_egress_info(egress, file_data)
        self._log_egress_info(egress_info)

        # Enqueue for transcription
        asyncio.create_task(self.transcription_service.enqueue(egress_info.dict()))

        return WebhookResponse(received=True, action="egress_ending_logged")

    async def _handle_participant_connection_aborted(self, event: Dict) -> WebhookResponse:
        """Handle when a participant connection is aborted"""
        logger.info(f"Participant connection aborted: {json.dumps(event)}")
        return WebhookResponse(received=True, action="participant_connection_aborted_logged")

    def _build_egress_info(self, egress: Dict, file_data: Dict) -> EgressInfo:
        """Build EgressInfo object from event data (simplified)"""
        filepath = file_data.get("filename")
        parsed = Filepath.parse(filepath)
        return EgressInfo(
            egressId=egress.get("egressId"),
            filename=file_data.get("filename"),
            source=parsed.get("source", ""),
            location=file_data.get("location", ""),
            duration=file_data.get("duration", 0),
            startedAt=file_data.get("startedAt"),
            endedAt=file_data.get("endedAt"),
        )

    def _log_egress_info(self, info: EgressInfo):
        """Log egress information"""
        logger.info(f"  Egress: {info.egressId}")
        logger.info(f"  File: {info.filename}")
        logger.info(f"  Location: {info.location}")
        logger.info(f"  Duration: {info.duration}ns")
        logger.info(f"  Started At: {info.startedAt}")
        logger.info(f"  Ended At: {info.endedAt}")
