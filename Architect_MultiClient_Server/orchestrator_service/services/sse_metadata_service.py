from typing import Optional, Dict, Any, Tuple, List
from datetime import datetime
from fastapi import HTTPException

from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.models.metadata_event_models import MetadataEventType
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

class SseMetadataService:
    def __init__(self, repository: PgTranscriptRepository, metadata_channel: MetadataChannel):
        self.pg_repo = repository
        self.metadata_channel = metadata_channel

    async def _ensure_connected(self):
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

    async def create_connection(self, user_id: str):
        return await self.metadata_channel.create_connection(user_id)

    async def push_room_started(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_started(room_id=room_id, room_name=room_name)

    async def push_room_ended(self, room_id: str, room_name: str, duration_seconds: Optional[int]):
        return await self.metadata_channel.push_room_ended(room_id=room_id, room_name=room_name, duration_seconds=duration_seconds)

    async def push_room_record_done(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_record_done(room_id=room_id, room_name=room_name)

    async def push_room_summary_done(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_summary_done(room_id=room_id, room_name=room_name)

    async def list_metadata_events(
        self,
        event_type: Optional[str],
        room_id: Optional[str],
        from_utc: Optional[datetime],
        to_utc: Optional[datetime],
        limit: int,
        skip: int,
        sort_order: str
    ) -> Tuple[List[Dict[str, Any]], int]:
        # Validate event_type
        if event_type is not None and MetadataEventType.is_valid(event_type) is False:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid event_type. Must be one of: {', '.join(MetadataEventType)}"
            )

        # Validate time range
        if from_utc is not None and to_utc is not None and from_utc >= to_utc:
            raise HTTPException(status_code=400, detail="from_utc must be before to_utc")

        # Validate sort_order
        if sort_order not in ["asc", "desc"]:
            raise HTTPException(status_code=400, detail="sort_order must be 'asc' (ascending) or 'desc' (descending)")
    
        await self._ensure_connected()

        events = await self.pg_repo.get_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc,
            limit=limit,
            skip=skip,
            sort_order=sort_order
        )

        total = await self.pg_repo.count_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc
        )

        return events, total

    async def get_metadata_event_by_id(self, event_id: str) -> Dict[str, Any]:
        await self._ensure_connected()

        event = await self.pg_repo.get_metadata_event_by_event_id(event_id)
        if not event:
            raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

        return event

# Get singleton instances
_metadata_channel = MetadataChannel()

def get_sse_metadata_service() -> SseMetadataService:
    repo = PgTranscriptRepository()
    return SseMetadataService(repository=repo, metadata_channel=_metadata_channel)