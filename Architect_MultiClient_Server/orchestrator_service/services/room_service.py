from orchestrator_service.services.livekit_client import LiveKitServiceError
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict
from orchestrator_service.auth.authorization import AuthContext
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository, get_pg_transcript_repository
from orchestrator_service.services.postgresql.models import Room
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)

class RoomService:
    def __init__(self, pg_repo: PgTranscriptRepository):
        self.pg_repo = pg_repo

    def _serialize_room(self, room: Room) -> dict:
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

    async def list_rooms(
        self,
        auth: AuthContext,
        status: Optional[str],
        search: Optional[str],
        from_utc: Optional[datetime],
        to_utc: Optional[datetime],
        limit: int,
        skip: int
    ) -> Tuple[List[dict], int]:
        if auth.can_view_all_rooms:
            rooms = await self.pg_repo.list_rooms(status, search, from_utc, to_utc, limit, skip)
            total = await self.pg_repo.count_rooms(status, search, from_utc, to_utc)
        else:
            rooms = await self.pg_repo.list_rooms_by_user(auth.user_id, status, search, from_utc, to_utc, limit, skip)
            total = await self.pg_repo.count_rooms_by_user(auth.user_id, status, search, from_utc, to_utc)

        return [self._serialize_room(room) for room in rooms], total

    async def get_room_by_id(
        self, 
        room_id: str, 
        auth: AuthContext
    ) -> dict:
        if not auth.can_view_all_rooms:
            has_access = await self.pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room {room_id}")
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        room = await self.pg_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(
                status_code=404, detail=f"Room with ID '{room_id}' not found"
            )

        return self._serialize_room(room)

    async def get_room_statistics(
        self, 
        room_id: str, 
        auth: AuthContext
    ) -> dict:
        if not auth.can_view_all_rooms:
            has_access = await self.pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        summary, room = await self.pg_repo.get_summary_by_room_id(room_id)
        tracks = await self.pg_repo.get_tracks_by_room(room_id)
        if not room:
            raise HTTPException(
                status_code=404, detail=f"Room with ID '{room_id}' not found"
            )

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

    async def get_audio_info(
        self,
        room_id: str,
        auth: AuthContext
    ) -> List[Dict[str, Any]]:
        if not auth.can_view_all_rooms:
            has_access = await self.pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        tracks = await self.pg_repo.get_tracks_by_room(room_id)
        if not tracks:
            raise HTTPException(
                status_code=404,
                detail=f"No tracks found for room with ID '{room_id}'",
            )
        
        file_results = []
        for track in tracks:
            audio_info = track.audio_info
            if not audio_info:
                continue
            file_results.append({
                "participant_identity": track.participant_identity,
                "filename": audio_info.get("filename", ""),
                "started_at_ns": audio_info.get("started_at_ns"),
                "ended_at_ns": audio_info.get("ended_at_ns"),
            })

        return file_results

    async def create_dispatch(self, room_name: str) -> dict:
        livekit_service = get_livekit_service()
        try:
            result = await livekit_service.ensure_dispatch(room_name)
            if result.get("dispatch") is not None:
                result["dispatch"] = MessageToDict(
                    result["dispatch"], preserving_proto_field_name=True
                )
            return result
        except LiveKitServiceError as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def cancel_dispatch(self, room_name: str) -> dict:
        livekit_service = get_livekit_service()
        try:
            result = await livekit_service.cancel_dispatch(room_name)
            if result.get("dispatch") is not None:
                result["dispatch"] = MessageToDict(
                    result["dispatch"], preserving_proto_field_name=True
                )
            return result
        except LiveKitServiceError as e:
            raise HTTPException(status_code=500, detail=str(e))

    async def list_participants(self, room_id: str) -> list:
        room = await self.pg_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        try:
            livekit_service = get_livekit_service()
            participants = await livekit_service.list_participants(room.room_name)
            return participants
        except LiveKitServiceError as e:
            raise HTTPException(status_code=500, detail=str(e))

# Get singleton instance
_room_service: RoomService | None = None

def get_room_service() -> RoomService:
    global _room_service
    if _room_service is None:
        _room_service = RoomService(
            pg_repo=get_pg_transcript_repository()
        )
    return _room_service