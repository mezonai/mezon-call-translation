"""
Metadata Channel for agent metadata events
Handles SSE connections for room lifecycle and recording events (global, not room-specific)
"""
from typing import Optional, Dict, Any
from datetime import datetime
import uuid
from fastapi.responses import StreamingResponse

from orchestrator_service.api.sse.sse_manager import SSEManager
from orchestrator_service.api.sse.sse_base import event_generator, create_sse_response
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.models.metadata_event_models import MetadataEventType

logger = get_logger(__name__)


@singleton
class MetadataChannel:
    """
    Metadata Channel for agent metadata events.
    Context: global (all bots receive the same events)
    
    Use case: All bots connect and receive the same metadata events about room lifecycle
    Events: room_started, room_ended, room_record_done, room_summary_done
    """
    
    CHANNEL_TYPE = "metadata"
    CONTEXT_KEY = "metadata_global"  # Single global context for all bots
    
    def __init__(self):
        """
        Initialize metadata channel with singleton dependencies.
        """
        self.manager = SSEManager()
        self.mongodb_service = MongoDBService()
    
    async def create_connection(
        self,
        appid: str
    ) -> StreamingResponse:
        """
        Create SSE connection for metadata channel.
        
        Args:
            appid: Application ID for authentication
        
        Returns:
            StreamingResponse with SSE events
        
        Raises:
            HTTPException: If authentication fails
        """
        context_key = self.CONTEXT_KEY
        
        # Close existing connection from same appid
        existing_disconnected = await self.manager.disconnect_existing_appid(
            self.CHANNEL_TYPE,
            context_key,
            appid
        )
        if existing_disconnected:
            logger.info(
                f"[Metadata Channel] Closed existing connection for appid {appid}"
            )
        
        # Register new connection
        connection_id = await self.manager.register_connection(
            self.CHANNEL_TYPE,
            context_key,
            appid
        )
        
        # Create dedicated queue
        connection_queue = await self.manager.create_connection_queue(
            self.CHANNEL_TYPE,
            context_key,
            connection_id
        )
        
        logger.info(
            f"[Metadata Channel] New connection registered: {connection_id} "
            f"(appid={appid}), "
            f"total: {await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key)}"
        )
        
        # Create SSE response
        return create_sse_response(
            event_generator(
                self.CHANNEL_TYPE,
                context_key,
                connection_id,
                connection_queue,
                self.manager
            )
        )
    
    def _create_base_event(
        self,
        event_type: str,
        room_id: str,
        room_name: str,
        metadata: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Create base event structure with unified format.
        
        Args:
            event_type: Type of event (room_started, room_ended, room_record_done)
            room_id: Room identifier
            room_name: Room name
            metadata: Event-specific metadata
        
        Returns:
            Unified event structure
        """
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": event_type,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "room": {
                "room_id": room_id,
                "room_name": room_name
            },
            "metadata": metadata
        }

    async def _save_event_to_db(self, event_data: Dict[str, Any]) -> Optional[str]:
        """
        Save metadata event to MongoDB with TTL.
        Events will be automatically deleted after 3 days.

        Args:
            event_data: Event data from _create_base_event

        Returns:
            Database _id as string if successful, None if failed
        """
        try:
            # Prepare document with event_id as _id and created_at for TTL
            doc = {
                "_id": event_data["event_id"],  # Use event_id as document _id
                "event_type": event_data["event_type"],
                "room_id": event_data["room"]["room_id"],
                "room_name": event_data["room"]["room_name"],
                "metadata": event_data["metadata"],
                "created_at": datetime.utcnow(),
                "timestamp": event_data["timestamp"]
            }
            event_db_id = await self.mongodb_service.save_metadata_event(doc)
            if event_db_id:
                logger.info(f"[Metadata Channel] Saved event to DB: {event_db_id}")
            return event_db_id
        except Exception as e:
            logger.error(f"[Metadata Channel] Failed to save event to DB: {e}")
            return None

    async def push_room_started(
        self,
        room_id: str,
        room_name: str
    ) -> Dict[str, Any]:
        """
        Push room_started event to all connected bots.
        
        Args:
            room_id: Room identifier
            room_name: Room name
        
        Returns:
            Dictionary with push status
        """
        context_key = self.CONTEXT_KEY
        
        # Check if any bots are connected
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(
                f"[Metadata Channel] No active bot connections, room_started event may be lost"
            )
        
        # Prepare event data
        event_data = self._create_base_event(
            event_type=MetadataEventType.ROOM_STARTED,
            room_id=room_id,
            room_name=room_name,
            metadata={}
        )
        
        # Broadcast event
        broadcast_count = await self.manager.broadcast_message(
            self.CHANNEL_TYPE,
            context_key,
            event_data
        )
        
        logger.info(
            f"[Metadata Channel] Pushed room_started event to {broadcast_count} bots "
            f"(room_id={room_id}, room_name={room_name})"
        )
        
        # Save event to MongoDB with TTL
        await self._save_event_to_db(event_data)

        return {
            "status": "ok",
            "event_type": MetadataEventType.ROOM_STARTED,
            "event_id": event_data["event_id"],
            "room_id": room_id,
            "room_name": room_name,
            "timestamp": event_data["timestamp"],
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count
        }
    
    async def push_room_ended(
        self,
        room_id: str,
        room_name: str,
        duration_seconds: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Push room_ended event to all connected bots.
        
        Args:
            room_id: Room identifier
            room_name: Room name
            duration_seconds: Optional duration of the room session in seconds
        
        Returns:
            Dictionary with push status
        """
        context_key = self.CONTEXT_KEY
        
        # Check if any bots are connected
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(
                f"[Metadata Channel] No active bot connections, room_ended event may be lost"
            )
        
        # Prepare metadata
        metadata = {}
        if duration_seconds is not None:
            metadata["duration_seconds"] = duration_seconds
        
        # Prepare event data
        event_data = self._create_base_event(
            event_type=MetadataEventType.ROOM_ENDED,
            room_id=room_id,
            room_name=room_name,
            metadata=metadata
        )
        
        # Broadcast event
        broadcast_count = await self.manager.broadcast_message(
            self.CHANNEL_TYPE,
            context_key,
            event_data
        )
        
        logger.info(
            f"[Metadata Channel] Pushed room_ended event to {broadcast_count} bots "
            f"(room_id={room_id}, room_name={room_name}, duration={duration_seconds}s)"
        )

        # Save event to MongoDB with TTL
        await self._save_event_to_db(event_data)

        return {
            "status": "ok",
            "event_type": MetadataEventType.ROOM_ENDED,
            "event_id": event_data["event_id"],
            "room_id": room_id,
            "room_name": room_name,
            "duration_seconds": duration_seconds,
            "timestamp": event_data["timestamp"],
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count
        }
    
    async def push_room_record_done(
        self,
        room_id: str,
        room_name: str
    ) -> Dict[str, Any]:
        """
        Push room_record_done event to all connected bots.
        Automatically fetches tracks from MongoDB and builds file_results.
        
        Args:
            room_id: Room identifier
            room_name: Room name
        
        Returns:
            Dictionary with push status
        """
        context_key = self.CONTEXT_KEY
        
        # Check if any bots are connected
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(
                f"[Metadata Channel] No active bot connections, room_record_done event may be lost"
            )
        
        # Fetch tracks from MongoDB and build file_results
        file_results = []
        try:
            tracks = await self.mongodb_service.get_tracks_by_room(room_id)
            
            for track in tracks:
                audio_info = track.get("audio_info", {})
                
                
                started_at_ns = audio_info.get("started_at_ns")
                ended_at_ns = audio_info.get("ended_at_ns")

                
                file_result = {
                    "participant_identity": track.get("participant_identity", ""),
                    "filename": audio_info.get("filename", ""),
                    "started_at_ns": started_at_ns,
                    "ended_at_ns": ended_at_ns
                }
                file_results.append(file_result)
            
            logger.info(
                f"[Metadata Channel] Built file_results from {len(tracks)} tracks for room {room_id}"
            )
        except Exception as e:
            logger.error(f"[Metadata Channel] Failed to fetch tracks for room {room_id}: {e}")
            # Continue with empty file_results
        
        # Prepare metadata
        metadata = {
            "file_results": file_results
        }
        
        # Prepare event data
        event_data = self._create_base_event(
            event_type=MetadataEventType.ROOM_RECORD_DONE,
            room_id=room_id,
            room_name=room_name,
            metadata=metadata
        )
        
        # Broadcast event
        broadcast_count = await self.manager.broadcast_message(
            self.CHANNEL_TYPE,
            context_key,
            event_data
        )
        
        logger.info(
            f"[Metadata Channel] Pushed room_record_done event to {broadcast_count} bots "
            f"(room_id={room_id}, room_name={room_name}, files={len(file_results)})"
        )

        # Save event to MongoDB with TTL
        await self._save_event_to_db(event_data)

        return {
            "status": "ok",
            "event_type": MetadataEventType.ROOM_RECORD_DONE,
            "event_id": event_data["event_id"],
            "room_id": room_id,
            "room_name": room_name,
            "file_count": len(file_results),
            "timestamp": event_data["timestamp"],
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count
        }
    
    async def push_room_summary_done(
        self,
        room_id: str,
        room_name: str,
    ) -> Dict[str, Any]:
        """
        Push room_summary_done event to all connected bots.
        Notifies that room summary/analysis has been completed.
        
        Args:
            room_id: Room identifier
            room_name: Room name
            status: Summary generation status (success/failure)
            summary_id: Optional identifier for the generated summary
        Returns:
            Dictionary with push status
        """
        context_key = self.CONTEXT_KEY
        
        # Check if any bots are connected
        if not await self.manager.has_active_connections(self.CHANNEL_TYPE, context_key):
            logger.warning(
                f"[Metadata Channel] No active bot connections, room_summary_done event may be lost"
            )
        
        # Prepare event data
        event_data = self._create_base_event(
            event_type=MetadataEventType.ROOM_SUMMARY_DONE,
            room_id=room_id,
            room_name=room_name,
            metadata={}
        )
        
        # Broadcast event
        broadcast_count = await self.manager.broadcast_message(
            self.CHANNEL_TYPE,
            context_key,
            event_data
        )
        
        logger.info(
            f"[Metadata Channel] Pushed room_summary_done event to {broadcast_count} bots "
            f"(room_id={room_id}, room_name={room_name})"
        )

        # Save event to MongoDB with TTL
        await self._save_event_to_db(event_data)

        return {
            "status": "ok",
            "event_type": MetadataEventType.ROOM_SUMMARY_DONE,
            "event_id": event_data["event_id"],
            "room_id": room_id,
            "room_name": room_name,
            "timestamp": event_data["timestamp"],
            "active_connections": await self.manager.get_connection_count(self.CHANNEL_TYPE, context_key),
            "broadcast_to": broadcast_count
        }
