"""
PostgreSQL repository for transcripts (replaces MongoDBService).
Handles rooms, tracks, chunks, summary, and metadata events.
"""

import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar, cast

from sqlalchemy import Select, exists, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import (
    MetadataEvent,
    Room,
    Track,
    TranscriptChunk,
)
from orchestrator_service.utils.logger import get_logger

_T = TypeVar("_T")

logger = get_logger(__name__)


class PgTranscriptRepository:
    """PostgreSQL-backed transcript repository. Drop-in for MongoDBService."""

    def __init__(self):
        self.connected = True  # Always "connected" via connection pool

    async def connect(self):
        self.connected = True
        return True

    async def disconnect(self):
        pass

    async def ping(self):
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                await session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # ROOMS
    # ------------------------------------------------------------------

    def _build_list_rooms_condition_query(
        self,
        stmt: Select[tuple[_T]],
        status: str | None = None,
        search: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> Select[tuple[_T]]:
        if status:
            stmt = stmt.where(Room.status == status)
        if from_utc:
            stmt = stmt.where(Room.created_at >= from_utc)
        if to_utc:
            stmt = stmt.where(Room.created_at <= to_utc)
        if search:
            search_term = f"%{search}%"

            stmt = stmt.where(
                or_(Room.room_name.ilike(search_term), Room.participants.contains([{"participant_identity": search}]))
            )

        return stmt

    async def list_rooms(
        self,
        status: str | None = None,
        search: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        limit: int = 10,
        skip: int = 0,
    ) -> list[Room]:
        session_factory = get_session_factory()
        stmt = select(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt, status=status, search=search, from_utc=from_utc, to_utc=to_utc
        )
        stmt = stmt.order_by(Room.created_at.desc()).limit(limit).offset(skip)

        try:
            async with session_factory() as session:
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to list rooms: {e}")
            return []

    async def count_rooms(
        self,
        status: str | None = None,
        search: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt, status=status, search=search, from_utc=from_utc, to_utc=to_utc
        )

        try:
            async with session_factory() as session:
                return await session.scalar(stmt) or 0
        except Exception as e:
            logger.error(f"Failed to count rooms: {e}")
            return 0

    async def get_room_by_id(self, room_id: str) -> Room | None:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                return await session.get(Room, room_id)
        except Exception as e:
            logger.error(f"Failed to get room by id: {e}")
            return None

    async def create_room_session(self, room_name: str, status: str = "pending") -> str | None:
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        uid = uuid.uuid4()
        try:
            async with session_factory() as session:
                new_room = Room(id=uid, room_name=room_name, status=status, participants=[], created_at=now)
                session.add(new_room)
                await session.commit()
                return str(new_room.id)
        except Exception as e:
            logger.error(f"Failed to create room: {e}")
            return None

    async def final_room_status(self, room_name: str, room_id: str) -> bool:
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                stmt = (
                    update(Room)
                    .where(Room.id == room_id, Room.status == "pending")
                    .values(status="final_room", finalized_at=now)
                    .returning(Room.id)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed to finalize room: {e}")
            return False

    async def save_participant(
        self, room_id: str, participant_identity: str, timestamp: datetime | None = None
    ) -> bool:
        session_factory = get_session_factory()
        ts = timestamp or datetime.now(UTC)

        new_participant = [{"participant_identity": participant_identity, "timestamp": ts.isoformat()}]

        try:
            async with session_factory() as session:
                stmt = (
                    update(Room)
                    .where(
                        Room.id == room_id,
                        ~Room.participants.contains([{"participant_identity": participant_identity}]),
                    )
                    .values(participants=Room.participants.concat(new_participant))
                )
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save participant: {e}")
            return False

    async def save_batch_participants(
        self, room_id: str, participants: list[dict[str, str | datetime]]
    ) -> dict[str, int]:
        if not participants:
            return {"success": True, "added_count": 0, "skipped_count": 0}

        session_factory = get_session_factory()

        try:
            async with session_factory() as session:
                room = await session.get(Room, room_id)
                if not room:
                    logger.error(f"Room {room_id} not found")
                    return {"success": False, "added_count": 0, "skipped_count": 0}

                # TODO: Use `Any` because the `Room.participants` field in the database model is mapped as a generic `dict`
                raw_participants = cast(Any, room.participants) or []  # type: ignore[explicit-any]

                existing_participants: list[dict[str, Any]] = [  # type: ignore[explicit-any]
                    p for p in raw_participants if isinstance(p, dict)
                ]

                existing_identities: set[str] = {
                    str(p.get("participant_identity")) for p in existing_participants if p.get("participant_identity")
                }

                participants_to_add = []
                skipped_count = 0

                for p in participants:
                    identity = p.get("participant_identity")

                    if not identity:
                        skipped_count += 1
                        continue

                    if identity in existing_identities:
                        skipped_count += 1
                        continue

                    ts = p.get("timestamp") or datetime.now(UTC)
                    if isinstance(ts, datetime):
                        ts = ts.isoformat()

                    participants_to_add.append(
                        {
                            "participant_identity": identity,
                            "timestamp": ts,
                        }
                    )

                if participants_to_add:
                    added_count = len(participants_to_add)

                    update_stmt = (
                        update(Room)
                        .where(Room.id == room_id)
                        .values(participants=Room.participants.concat(participants_to_add))
                    )

                    await session.execute(update_stmt)
                    await session.commit()
                    logger.info(f"✅ Batch save to room {room_id}: Added {added_count}, Skipped {skipped_count}")

                    return {
                        "success": True,
                        "added_count": added_count,
                        "skipped_count": skipped_count,
                    }
                else:
                    logger.info(f"No new participants to add to room {room_id}")
                    return {"success": True, "added_count": 0, "skipped_count": skipped_count}

        except Exception as e:
            logger.error(f"❌ Error in save_batch_participants: {e}")
            return {"success": False, "added_count": 0, "skipped_count": 0}

    # TODO: Use `Any` type because `room_participants` input field from generate_summary() in SummaryService
    # has list[dict[str, Any]] type
    async def update_room_participants(  # type: ignore[explicit-any]
        self, room_id: str, participants: list[dict[str, Any]]
    ) -> bool:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                update_stmt = update(Room).where(Room.id == room_id).values(participants=participants)
                await session.execute(update_stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update room participants: {e}")
            return False

    # ------------------------------------------------------------------
    # TRACKS
    # ------------------------------------------------------------------

    async def get_tracks_by_room(self, room_id: str, status: str | None = None) -> list[Track]:
        try:
            uid = str(uuid.UUID(room_id))
        except (ValueError, AttributeError):
            logger.warning(f"get_tracks_by_room: invalid UUID format repr={room_id!r}, returning empty")
            return []
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(Track).where(Track.room_ref_id == uid)
                if status:
                    stmt = stmt.where(Track.status == status)
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to get tracks by room: {e}")
            return []

    async def get_track_by_id(self, track_id: str) -> Track | None:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                return await session.get(Track, track_id)
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None

    async def update_track_status(self, track_ref_id: str, status: str) -> dict[str, str | Track | bool]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(Track)
                    .where(Track.id == track_ref_id)
                    .values(status=status, updated_at=datetime.now(UTC))
                    .returning(Track)
                )
                result = await session.execute(stmt)
                updated_track = result.scalar_one_or_none()
                await session.commit()

                if updated_track:
                    return {"success": True, "track": updated_track}
                return {"success": False, "error": "Not found"}
        except Exception as e:
            logger.error(f"Failed to update track status: {e}")
            return {"success": False, "error": str(e)}

    async def check_event_record_done(self, room_ref_id: str) -> Room | None:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt_room = select(Room).where(Room.id == room_ref_id, Room.status == "final_room")
                room = await session.scalar(stmt_room)

                if not room:
                    logger.info(f"No room found for room_ref_id={room_ref_id} with status 'final_room'")
                    return None

                stmt_track = (
                    select(func.count())
                    .select_from(Track)
                    .where(Track.room_ref_id == room_ref_id, Track.status == "pending")
                )
                pending_count = await session.scalar(stmt_track) or 0

                if pending_count == 0:
                    return room
                return None
        except Exception as e:
            logger.error(f"Failed check event record done: {e}")
            return None

    async def check_and_complete_room(self, room_ref_id: str) -> bool:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt_count = (
                    select(func.count())
                    .select_from(Track)
                    .where(Track.room_ref_id == room_ref_id, Track.status.in_(["pending", "wait_process"]))
                )
                pending_count = await session.scalar(stmt_count) or 0
                if pending_count > 0:
                    logger.debug(f"Room still has {pending_count} incomplete tracks")
                    return False

                stmt_update = (
                    update(Room)
                    .where(Room.id == room_ref_id, Room.status == "final_room")
                    .values(status="completed", completed_at=datetime.now(UTC))
                    .returning(Room)
                )
                result = await session.execute(stmt_update)
                await session.commit()
                return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed check and complete room: {e}")
            return False

    async def user_has_room_access(self, room_id: str, user_id: str) -> bool:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(
                    exists().where(Room.id == room_id, Room.participants.contains([{"participant_identity": user_id}]))
                )
                has_access = await session.scalar(stmt)

                logger.debug(f"user_has_room_access: user_id={user_id}, room_id={room_id}, has_access={has_access}")

                return bool(has_access)

        except Exception as e:
            logger.error(f"Failed to check user room access: {e}")
            return False

    async def list_rooms_by_user(
        self,
        user_id: str,
        status: str | None = None,
        search: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        limit: int = 10,
        skip: int = 0,
    ) -> list[Room]:
        session_factory = get_session_factory()
        stmt = select(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt, status=status, search=search, from_utc=from_utc, to_utc=to_utc
        )
        stmt = stmt.where(Room.participants.contains([{"participant_identity": user_id}]))
        stmt = stmt.order_by(Room.created_at.desc()).limit(limit).offset(skip)

        try:
            async with session_factory() as session:
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to list rooms by user: {e}")
            return []

    async def count_rooms_by_user(
        self,
        user_id: str,
        status: str | None = None,
        search: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt, status=status, search=search, from_utc=from_utc, to_utc=to_utc
        )
        stmt = stmt.where(Room.participants.contains([{"participant_identity": user_id}]))

        try:
            async with session_factory() as session:
                return await session.scalar(stmt) or 0
        except Exception as e:
            logger.error(f"Failed to count rooms by user: {e}")
            return 0

    # ------------------------------------------------------------------
    # METADATA EVENTS
    # ------------------------------------------------------------------

    async def save_metadata_event(
        self, event_data: dict[str, str | int | dict[str, int] | dict[str, str] | datetime]
    ) -> str | None:
        session_factory = get_session_factory()
        uid = uuid.uuid4()
        room_uid = event_data.get("room_id")
        try:
            async with session_factory() as session:
                stmt = insert(MetadataEvent).values(
                    id=uid,
                    event_id=event_data.get("event_id"),
                    event_type=event_data.get("event_type"),
                    room_id=room_uid,
                    room_name=event_data.get("room_name"),
                    event_metadata=event_data.get("metadata", {}),
                    timestamp=str(event_data.get("timestamp")),
                    created_at=datetime.now(UTC),
                )
                stmt = stmt.on_conflict_do_nothing(index_elements=[MetadataEvent.event_id])
                await session.execute(stmt)
                await session.commit()
                return str(uid)
        except Exception as e:
            logger.error(f"Failed to save event: {e}")
            return None

    async def get_metadata_event_by_event_id(self, event_id: str) -> MetadataEvent | None:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(MetadataEvent).where(MetadataEvent.event_id == event_id)
                res: MetadataEvent | None = await session.scalar(stmt)
                return res
        except Exception as e:
            logger.error(f"Failed to get event by id: {e}")
            return None

    async def append_transcript_chunk(
        self, track_ref_id: str, new_segments: list[dict[str, float | str | None]]
    ) -> bool:
        """Append new segments as additional chunks"""
        if not new_segments:
            return True

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    select(TranscriptChunk.chunk_index)
                    .where(TranscriptChunk.track_ref_id == track_ref_id)
                    .order_by(TranscriptChunk.chunk_index.desc())
                    .limit(1)
                )
                last_chunk_index = await session.scalar(stmt)
                start_index = (last_chunk_index + 1) if last_chunk_index is not None else 0

                # Assume self._split_into_chunks exists or just dump everything if not available
                # Actually, Mongo implementation has self._split_into_chunks and self._create_chunk_document.
                # I'll replicate the logic or just insert them directly.
                # In Mongo, chunks are split. We'll implement a basic splitter here if needed,
                # or just insert as one chunk for now.
                # To match exactly:
                chunk_size = 50
                chunks = [new_segments[i : i + chunk_size] for i in range(0, len(new_segments), chunk_size)]

                chunk_documents = []
                for i, chunk_segments in enumerate(chunks):
                    start_time = chunk_segments[0].get("start", 0.0) if chunk_segments else 0.0
                    end_time = chunk_segments[-1].get("end", 0.0) if chunk_segments else 0.0
                    new_chunk = TranscriptChunk(
                        id=uuid.uuid4(),
                        track_ref_id=track_ref_id,
                        chunk_index=start_index + i,
                        start_time=start_time,
                        end_time=end_time,
                        item_count=len(chunk_segments),
                        segments=chunk_segments,
                    )
                    chunk_documents.append(new_chunk)

                if chunk_documents:
                    session.add_all(chunk_documents)

                    update_stmt = (
                        update(Track)
                        .where(Track.id == track_ref_id)
                        .values(chunk_count=Track.chunk_count + len(chunk_documents))
                    )

                    await session.execute(update_stmt)

                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to append chunks: {e}")
            return False

    def _build_metadata_events_condition_query(
        self,
        stmt: Select[tuple[_T]],
        event_type: str | None = None,
        room_id: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> Select[tuple[_T]]:
        if room_id:
            stmt = stmt.where(MetadataEvent.room_id == room_id)
        if event_type:
            stmt = stmt.where(MetadataEvent.event_type == event_type)
        if from_utc:
            stmt = stmt.where(MetadataEvent.created_at >= from_utc)
        if to_utc:
            stmt = stmt.where(MetadataEvent.created_at <= to_utc)

        return stmt

    async def get_metadata_events(
        self,
        event_type: str | None = None,
        room_id: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
        limit: int = 100,
        skip: int = 0,
        sort_order: str = "desc",
    ) -> list[MetadataEvent]:
        session_factory = get_session_factory()
        stmt = select(MetadataEvent)
        stmt = self._build_metadata_events_condition_query(
            stmt=stmt, event_type=event_type, room_id=room_id, from_utc=from_utc, to_utc=to_utc
        )

        if sort_order.lower() == "asc":
            stmt = stmt.order_by(MetadataEvent.created_at.asc())
        else:
            stmt = stmt.order_by(MetadataEvent.created_at.desc())

        stmt = stmt.limit(limit).offset(skip)

        try:
            async with session_factory() as session:
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to get metadata events: {e}")
            return []

    async def count_metadata_events(
        self,
        event_type: str | None = None,
        room_id: str | None = None,
        from_utc: datetime | None = None,
        to_utc: datetime | None = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(MetadataEvent)
        stmt = self._build_metadata_events_condition_query(
            stmt=stmt, event_type=event_type, room_id=room_id, from_utc=from_utc, to_utc=to_utc
        )

        try:
            async with session_factory() as session:
                return await session.scalar(stmt) or 0
        except Exception as e:
            logger.error(f"Failed to count metadata events: {e}")
            return 0

    async def get_chunks_by_track(
        self,
        track_id: str,
        sorted_by_index: bool = True,
        limit: int | None = None,
        skip: int = 0,
    ) -> list[TranscriptChunk]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(TranscriptChunk).where(TranscriptChunk.track_ref_id == track_id)
                if sorted_by_index:
                    stmt = stmt.order_by(TranscriptChunk.chunk_index.asc())
                if limit is not None:
                    stmt = stmt.limit(limit)
                if skip > 0:
                    stmt = stmt.offset(skip)
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to get chunks by track: {e}")
            return []

    async def get_chunks_by_track_ids(
        self,
        track_ids: list[str],
        sorted_by_index: bool = True,
    ) -> list[TranscriptChunk]:
        if not track_ids:
            return []

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(TranscriptChunk).where(TranscriptChunk.track_ref_id.in_(track_ids))
                if sorted_by_index:
                    stmt = stmt.order_by(TranscriptChunk.chunk_index.asc())
                return list((await session.scalars(stmt)).all())
        except Exception as e:
            logger.error(f"Failed to get chunks by track ids: {e}")
            return []

    async def save_track_metadata(
        self,
        *,
        egress_id: str | None = None,
        track_id: str | None = None,
        room_ref_id: str | None = None,
        participant_identity: str | None = None,
        audio_info: dict[str, str | None] | None = None,
        status: str = "pending",
        error: str | None = None,
    ) -> Track | None:
        if not egress_id:
            logger.error("egress_id is required")
            return None

        session_factory = get_session_factory()
        now = datetime.now(UTC)

        try:
            async with session_factory() as session:
                track = await session.get(Track, egress_id)
                if track:
                    track.updated_at = now
                    if status:
                        track.status = status
                    if audio_info is not None:
                        track.audio_info = audio_info
                    if error is not None:
                        track.error = error
                else:
                    track = Track(
                        id=egress_id,
                        track_id=track_id,
                        room_ref_id=room_ref_id,
                        participant_identity=participant_identity,
                        status=status,
                        audio_info=audio_info,
                        error=error,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(track)
                await session.commit()

                logger.info(f"📝 Track metadata saved: id(egress)={egress_id}")

                return track
        except Exception as e:
            logger.error(f"Failed to save track metadata: {e}")
            return None


# --------------- Singleton ---------------
_pg_transcript_repository: PgTranscriptRepository | None = None


def get_pg_transcript_repository() -> PgTranscriptRepository:
    global _pg_transcript_repository
    if _pg_transcript_repository is None:
        _pg_transcript_repository = PgTranscriptRepository()
    return _pg_transcript_repository
