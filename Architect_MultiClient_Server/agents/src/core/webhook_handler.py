
import asyncio
import json
from typing import Dict, Any
from src.logger import get_logger
from src.services.egress_service import EgressService
from src.services.transcription_service import TranscriptionService
from src.utils.filepath_parser import FilepathParser
from src.models.webhook_models import WebhookResponse, TrackInfo, EgressInfo

logger = get_logger(__name__)


class WebhookHandler:
    """Webhook event handler from LiveKit"""
    
    def __init__(
        self, 
        egress_service: EgressService,
        transcription_service: TranscriptionService
    ):
        self.egress_service = egress_service
        self.transcription_service = transcription_service
    
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
        logger.debug(f"Payload: {json.dumps(event, indent=2, ensure_ascii=False)}")
        
        handlers = {
            "track_published": self._handle_track_published,
            "track_unpublished": self._handle_track_unpublished,
            "participant_joined": self._handle_participant_joined,
            "participant_left": self._handle_participant_left,
            "room_started": self._handle_room_started,
            "room_finished": self._handle_room_finished,
            "egress_ended": self._handle_egress_ended,
        }
        
        handler = handlers.get(event_type)
        if handler:
            return await handler(event)
        
        logger.info(f"  (ignored)")
        return WebhookResponse(received=True, action="ignored")
    
    async def _handle_track_published(self, event: Dict) -> WebhookResponse:
        """Handle when a track is published"""
        room_name = event.get("room", {}).get("name", "unknown")
        identity = event.get("participant", {}).get("identity", "unknown")
        
        track_data = event.get("track", {})
        track = TrackInfo(
            sid=track_data.get("sid", ""),
            mime_type=track_data.get("mimeType", ""),
            source=track_data.get("source", "UNKNOWN")
        )
        
        logger.info(f"  Room: {room_name}")
        logger.info(f"  Participant: {identity}")
        logger.info(f"  Track: {track.sid} (mime: {track.mime_type}, source: {track.source})")
        
        if track.is_audio:
            asyncio.create_task(
                self.egress_service.start_recording(
                    room_name, track.sid, track.track_type, track.source, identity
                )
            )
            return WebhookResponse(received=True, action="recording_started")
        
        logger.info(f"  ⏭ Skipping {track.track_type}")
        return WebhookResponse(received=True, action=f"skipped_{track.track_type.lower()}")
    
    async def _handle_track_unpublished(self, event: Dict) -> WebhookResponse:
        """Handle when a track is unpublished"""
        track_sid = event.get("track", {}).get("sid", "")
        
        if self.egress_service.mark_unpublished(track_sid):
            logger.info(f"  Track {track_sid} unpublished, egress auto-stop")
            return WebhookResponse(received=True, action="egress_removed")
        
        return WebhookResponse(received=True, action="no_active_egress")
    
    async def _handle_participant_joined(self, event: Dict) -> WebhookResponse:
        """Handle when a participant joins"""
        identity = event.get("participant", {}).get("identity", "unknown")
        room_name = event.get("room", {}).get("name", "unknown")
        logger.info(f"  Participant joined: {identity} in {room_name}")
        return WebhookResponse(received=True, action="participant_joined_logged")
    
    async def _handle_participant_left(self, event: Dict) -> WebhookResponse:
        """Handle when a participant leaves"""
        identity = event.get("participant", {}).get("identity", "unknown")
        room_name = event.get("room", {}).get("name", "unknown")
        logger.info(f"  Participant left: {identity} from {room_name}")
        return WebhookResponse(received=True, action="participant_left_logged")
    
    async def _handle_room_started(self, event: Dict) -> WebhookResponse:
        """Handle when a room starts"""
        room_name = event.get("room", {}).get("name", "unknown")
        logger.info(f"  Room started: {room_name}")
        return WebhookResponse(received=True, action="room_started_logged")
    
    async def _handle_room_finished(self, event: Dict) -> WebhookResponse:
        """Handle when a room finishes"""
        room_name = event.get("room", {}).get("name", "unknown")
        logger.info(f"  Room finished: {room_name}")
        await self.transcription_service.final_room(room_name)
        return WebhookResponse(received=True, action="room_finished_logged")
    
    async def _handle_egress_ended(self, event: Dict) -> WebhookResponse:
        """Handle when an egress ends"""
        egress = event.get("egressInfo", {})
        status = egress.get("status", "unknown")
        
        if status != "EGRESS_COMPLETE":
            logger.info(f"Egress not completed: {status}")
            return WebhookResponse(received=True, action="egress_not_completed")
        
        file_data = egress.get("file", {})
        filename = file_data.get("filename", "")
        
        try:
            parsed = FilepathParser.parse(filename)
        except ValueError as e:
            logger.error(f"Failed to parse filename: {e}")
            return WebhookResponse(received=False, error=str(e))
        
        egress_info = self._build_egress_info(egress, file_data, parsed)
        self._log_egress_info(egress_info)
        
        # Enqueue for transcription
        asyncio.create_task(
            self.transcription_service.enqueue(egress_info.dict())
        )
        
        return WebhookResponse(received=True, action="egress_ending_logged")
    
    def _build_egress_info(self, egress: Dict, file_data: Dict, 
                          parsed: Dict) -> EgressInfo:
        """Build EgressInfo object from event data"""
        return EgressInfo(
            egressId=egress.get("egressId", "unknown"),
            room={"name": egress.get("roomName", "")},
            participant={"identity": parsed.get("identity", "unknown")},
            track={
                "id": egress.get("track", {}).get("trackId", "unknown"),
                "type": parsed.get("track_type", "unknown"),
                "source": parsed.get("source", "unknown")
            },
            audio={
                "filename": file_data.get("filename", ""),
                "location": file_data.get("location", ""),
                "duration": file_data.get("duration", 0)
            },
            timeline={
                "startedAt": file_data.get("startedAt", ""),
                "endedAt": file_data.get("endedAt", "")
            }
        )
    
    def _log_egress_info(self, info: EgressInfo):
        """Log egress information"""
        logger.info(f"  Egress: {info.egressId}")
        logger.info(f"  Room: {info.room['name']}")
        logger.info(f"  Participant: {info.participant['identity']}")
        logger.info(f"  Track: {info.track['id']} ({info.track['type']})")
        logger.info(f"  File: {info.audio['filename']}")
        logger.info(f"  Duration: {info.audio['duration']}s")

