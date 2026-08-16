from datetime import datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel, Field

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
    get_pg_transcript_repository,
)
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)


class MetadataEventResponse(BaseModel):  # type: ignore[explicit-any]
    """Data Transfer Object for Metadata Event (distinguish with ORM model)"""

    id: str = Field(description="Event primary key")
    event_id: str | None = Field(default=None, description="Event UUID")
    event_type: str | None = Field(default=None, description="Event Type")
    room_id: str | None = Field(default=None, description="Room ID")
    room_name: str | None = Field(default=None, description="Room Name")

    # TODO: Use `Any` type because metadata can have dynamic structures
    metadata: dict[str, Any] | None = Field(default=None, description="Metadata")  # type: ignore[explicit-any]

    timestamp: str | None = Field(default=None, description="Timestamp")
    created_at: str | None = Field(default=None, description="Created At")


class SseMetadataService:
    def __init__(self, pg_repo: PgTranscriptRepository, metadata_channel: MetadataChannel):
        self.pg_repo = pg_repo
        self.metadata_channel = metadata_channel

    async def create_connection(self, user_id: str):
        return await self.metadata_channel.create_connection(user_id)

    async def push_room_started(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_started(room_id=room_id, room_name=room_name)

    async def push_room_ended(self, room_id: str, room_name: str, duration_seconds: int | None):
        return await self.metadata_channel.push_room_ended(
            room_id=room_id, room_name=room_name, duration_seconds=duration_seconds
        )

    async def push_room_record_done(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_record_done(room_id=room_id, room_name=room_name)

    async def push_room_summary_done(self, room_id: str, room_name: str):
        return await self.metadata_channel.push_room_summary_done(room_id=room_id, room_name=room_name)

    async def list_metadata_events(
        self,
        event_type: str | None,
        room_id: str | None,
        from_utc: datetime | None,
        to_utc: datetime | None,
        limit: int,
        skip: int,
        sort_order: str,
    ) -> tuple[list[MetadataEventResponse], int]:
        raw_events = await self.pg_repo.get_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc,
            limit=limit,
            skip=skip,
            sort_order=sort_order,
        )

        total = await self.pg_repo.count_metadata_events(
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc,
        )

        events = []
        for event in raw_events:
            e = MetadataEventResponse(
                id=str(event.id),
                event_id=event.event_id,
                event_type=event.event_type,
                room_id=str(event.room_id) if event.room_id else None,
                room_name=event.room_name,
                metadata=event.event_metadata,
                timestamp=event.timestamp,
                created_at=convert_to_iso_8601(event.created_at) if isinstance(event.created_at, datetime) else None,
            )

            events.append(e)

        return events, total

    async def get_metadata_event_by_id(self, event_id: str) -> MetadataEventResponse:
        event_obj = await self.pg_repo.get_metadata_event_by_event_id(event_id)
        if not event_obj:
            raise HTTPException(status_code=404, detail=f"Event not found: {event_id}")

        return MetadataEventResponse(
            id=str(event_obj.id),
            event_id=event_obj.event_id,
            event_type=event_obj.event_type,
            room_id=str(event_obj.room_id) if event_obj.room_id else None,
            room_name=event_obj.room_name,
            metadata=event_obj.event_metadata,
            timestamp=event_obj.timestamp,
            created_at=convert_to_iso_8601(event_obj.created_at) if isinstance(event_obj.created_at, datetime) else None,
        )


# Get singleton instances
_metadata_channel = None
_sse_metadata_service: SseMetadataService | None = None


def get_metadata_channel() -> MetadataChannel:
    global _metadata_channel
    if _metadata_channel is None:
        _metadata_channel = MetadataChannel()
    return _metadata_channel


def get_sse_metadata_service() -> SseMetadataService:
    global _sse_metadata_service
    if _sse_metadata_service is None:
        _sse_metadata_service = SseMetadataService(
            pg_repo=get_pg_transcript_repository(), metadata_channel=get_metadata_channel()
        )
    return _sse_metadata_service
