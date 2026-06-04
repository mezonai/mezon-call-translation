from orchestrator_service.services.livekit_client import LiveKitServiceError
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict
from orchestrator_service.auth.authorization import AuthContext
from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)

class RoomService:
    def __init__(self, repository: PgTranscriptRepository):
        self.pg_repo = repository

    async def _ensure_connected(self):
        if not self.pg_repo.connected:
            await self.pg_repo.connect()

    def _serialize_room(self, room: dict) -> dict:
        serialized_room = dict(room)
        if serialized_room.get("id") is not None:
            serialized_room["id"] = str(serialized_room["id"])
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
        await self._ensure_connected()

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
        await self._ensure_connected()

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
        await self._ensure_connected()

        if not auth.can_view_all_rooms:
            has_access = await self.pg_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(
                    f"User {auth.user_id} denied access to room statistics for {room_id}"
                )
                raise HTTPException(
                    status_code=403, detail="You don't have access to this room"
                )

        stats = await self.pg_repo.get_room_statistics_by_id(room_id)
        if not stats:
            raise HTTPException(
                status_code=404, detail=f"Room with ID '{room_id}' not found"
            )

        if stats.get("room_id") is not None:
            stats["room_id"] = str(stats["room_id"])
        if stats.get("created_at") is not None:
            stats["created_at"] = convert_to_iso_8601(stats["created_at"])
        if stats.get("finalized_at") is not None:
            stats["finalized_at"] = convert_to_iso_8601(stats["finalized_at"])

        return stats

    async def get_audio_info(
        self,
        room_id: str,
        auth: AuthContext
    ) -> List[Dict[str, Any]]:
        await self._ensure_connected()

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
            audio_info = track.get("audio_info", {})
            file_results.append({
                "participant_identity": track.get("participant_identity"),
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
        await self._ensure_connected()

        room = await self.pg_repo.get_room_by_id(room_id)
        if not room:
            raise HTTPException(status_code=404, detail="Room not found")

        try:
            livekit_service = get_livekit_service()
            participants = await livekit_service.list_participants(room.get("room_name"))
            return participants
        except LiveKitServiceError as e:
            raise HTTPException(status_code=500, detail=str(e))

def get_room_service() -> RoomService:
    repo = PgTranscriptRepository()
    return RoomService(repository=repo)