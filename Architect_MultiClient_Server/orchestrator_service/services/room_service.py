from datetime import datetime
from typing import Any

from fastapi import HTTPException

from orchestrator_service.auth.authorization import AuthContext
from orchestrator_service.models.room_models import AudioTrackInfo
from orchestrator_service.services.postgresql.models import Room
from orchestrator_service.services.postgresql.pg_summary_repository import (
    PgSummaryRepository,
    get_pg_summary_repository,
)
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
    get_pg_transcript_repository,
)
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)


class RoomService:
    def __init__(self, pg_transcript_repo: PgTranscriptRepository, pg_summary_repo: PgSummaryRepository):
        self.pg_transcript_repo = pg_transcript_repo
        self.pg_summary_repo = pg_summary_repo

    # TODO: Use `Any` type because `serialized_room` is defined by complex types
    def _serialize_room(self, room: Room) -> dict[str, Any]:  # type: ignore[explicit-any]
        serialized_room = {
            "id": room.id,
            "room_name": room.room_name,
            "status": room.status,
            "participants": room.participants,
            "created_at": room.created_at,
            "finalized_at": room.finalized_at,
            "completed_at": room.completed_at,
        }
        return serialized_room

    # TODO: Use `Any` type because the return value has a tuple of complex types (call _serialize_room())
    async def list_rooms(  # type: ignore[explicit-any]
        self,
        auth: AuthContext,
        status: str | None,
        search: str | None,
        from_utc: datetime | None,
        to_utc: datetime | None,
        limit: int,
        skip: int,
    ) -> tuple[list[dict[str, Any]], int]:
        if auth.can_view_all_rooms:
            rooms = await self.pg_transcript_repo.list_rooms(status, search, from_utc, to_utc, limit, skip)
            total = await self.pg_transcript_repo.count_rooms(status, search, from_utc, to_utc)
        else:
            rooms = await self.pg_transcript_repo.list_rooms_by_user(
                auth.user_id, status, search, from_utc, to_utc, limit, skip
            )
            total = await self.pg_transcript_repo.count_rooms_by_user(auth.user_id, status, search, from_utc, to_utc)

        return [self._serialize_room(room) for room in rooms], total

    # TODO: Use `Any` type because the return value has a tuple of complex types (call _serialize_room())
    async def get_room_by_id(self, room_id: str, auth: AuthContext) -> dict[str, Any]:  # type: ignore[explicit-any]
        if not auth.can_view_all_rooms:
            has_access = await self.pg_transcript_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room {room_id}")
                raise HTTPException(status_code=403, detail="You don't have access to this room")

        room = await self.pg_transcript_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")

        return self._serialize_room(room)

    # TODO: Use `Any` type because the return dictionary is defined by complex types
    async def get_room_statistics(self, room_id: str, auth: AuthContext) -> dict[str, Any]:  # type: ignore[explicit-any]
        if not auth.can_view_all_rooms:
            has_access = await self.pg_transcript_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room statistics for {room_id}")
                raise HTTPException(status_code=403, detail="You don't have access to this room")

        summary, room = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        tracks = await self.pg_transcript_repo.get_tracks_by_room(room_id)
        if not room:
            raise HTTPException(status_code=404, detail=f"Room with ID '{room_id}' not found")

        total_segments = summary.total_segments if summary else 0
        total_tracks = len(tracks)
        completed_tracks = sum(1 for t in tracks if t.status == "completed")

        total_duration_sec: float = 0.0
        if room.finalized_at and room.created_at:
            total_duration_sec = (room.finalized_at - room.created_at).total_seconds()

        return {
            "room_id": room.id,
            "room_name": room.room_name,
            "status": room.status,
            "total_tracks": total_tracks,
            "completed_tracks": completed_tracks,
            "remaining_tracks": total_tracks - completed_tracks,
            "total_segments": total_segments,
            "created_at": convert_to_iso_8601(room.created_at) if room.created_at else None,
            "finalized_at": convert_to_iso_8601(room.finalized_at) if room.finalized_at else None,
            "total_duration_sec": total_duration_sec,
        }

    async def get_audio_info(self, room_id: str, auth: AuthContext) -> list[AudioTrackInfo]:
        if not auth.can_view_all_rooms:
            has_access = await self.pg_transcript_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room statistics for {room_id}")
                raise HTTPException(status_code=403, detail="You don't have access to this room")

        tracks = await self.pg_transcript_repo.get_tracks_by_room(room_id)
        if not tracks:
            raise HTTPException(
                status_code=404,
                detail=f"No tracks found for room with ID '{room_id}'",
            )

        file_results: list[AudioTrackInfo] = []
        for track in tracks:
            audio_info = track.audio_info
            if not audio_info:
                continue
            # "filename" here is the client-playable derivative (OGG/Opus),
            # not the raw PCM capture (audio-ingestion PLAN.md D28) -- same
            # field name/shape as before on purpose, so nothing downstream
            # needs to change, it just now points at something the client
            # can actually play instead of headerless raw PCM (D6). Written
            # by audio-processing-service's derivative.completed report,
            # see pg_track_repository.py::update_track_derivative. Skip the
            # track entirely (rather than fall back to the raw filename) if
            # the derivative isn't ready yet -- a raw PCM path isn't
            # something the client can use either way, so there is nothing
            # correct to show until it exists (D19: client polls this API
            # again after room_record_done fires).
            derivative_filename = audio_info.get("derivative_object_key")
            if not derivative_filename:
                continue
            file_results.append(
                AudioTrackInfo(
                    participant_identity=str(track.participant_identity),
                    filename=derivative_filename,
                    started_at_ns=audio_info.get("started_at_ns"),
                    ended_at_ns=audio_info.get("ended_at_ns"),
                )
            )

        return file_results


# Get singleton instance
_room_service: RoomService | None = None


def get_room_service() -> RoomService:
    global _room_service
    if _room_service is None:
        _room_service = RoomService(
            pg_transcript_repo=get_pg_transcript_repository(), pg_summary_repo=get_pg_summary_repository()
        )
    return _room_service
