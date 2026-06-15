"""
PostgreSQL repository for transcripts (replaces MongoDBService).
Handles rooms, tracks, chunks, summary, and metadata events.
"""

import json
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any, Tuple, Set
import uuid

from sqlalchemy import text, select, update, exists, func, Select, or_
from sqlalchemy.dialects.postgresql import insert
from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import Room, Track, RoomSummary, MetadataEvent, TranscriptChunk
from orchestrator_service.utils.logger import get_logger

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
        stmt: Select,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> Select:
        if status:
            stmt = stmt.where(Room.status == status)
        if from_utc:
            stmt = stmt.where(Room.created_at >= from_utc)
        if to_utc:
            stmt = stmt.where(Room.created_at <= to_utc)
        if search:
            search_term = f"%{search}%"

            stmt = stmt.where(
                or_(
                    Room.room_name.ilike(search_term),
                    Room.participants.contains([{"participant_identity": search}])
                )
            )

        return stmt

    async def list_rooms(
        self,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 10,
        skip: int = 0,
    ) -> List[Room]:
        session_factory = get_session_factory()
        stmt = select(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt,
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
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
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt,
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )

        try:
            async with session_factory() as session:
                return await session.scalar(stmt) or 0
        except Exception as e:
            logger.error(f"Failed to count rooms: {e}")
            return 0

    async def get_room_by_id(self, room_id: str) -> Optional[Room]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                return await session.get(Room, room_id)
        except Exception as e:
            logger.error(f"Failed to get room by id: {e}")
            return None

    async def create_room_session(
        self, room_name: str, status: str = "pending"
    ) -> Optional[str]:
        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)
        uid = uuid.uuid4()
        try:
            async with session_factory() as session:
                new_room = Room(
                    id=uid,
                    room_name=room_name,
                    status=status,
                    participants=[],
                    created_at=now
                )
                session.add(new_room)
                await session.commit()
                return str(new_room.id)
        except Exception as e:
            logger.error(f"Failed to create room: {e}")
            return None

    async def final_room_status(self, room_name: str, room_id: str) -> bool:
        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)
        try:
            async with session_factory() as session:
                stmt = (
                    update(Room)
                    .where(Room.id == room_id, Room.status == 'pending')
                    .values(
                        status='final_room',
                        finalized_at=now
                    )
                    .returning(Room.id)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed to finalize room: {e}")
            return False

    async def save_participant(
        self, room_id: str, participant_identity: str, timestamp: Optional[datetime] = None
    ) -> bool:
        session_factory = get_session_factory()
        ts = timestamp or datetime.now(timezone.utc)

        new_participant = [{
            "participant_identity": participant_identity,
            "timestamp": ts.isoformat()
        }]

        try:
            async with session_factory() as session:
                stmt = (
                    update(Room)
                    .where(
                        Room.id == room_id,
                        ~Room.participants.contains([{"participant_identity": participant_identity}])
                    )
                    .values(
                        participants=Room.participants.concat(new_participant)
                    )
                )
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to save participant: {e}")
            return False

    async def save_batch_participants(
        self, room_id: str, participants: List[Dict[str, Any]]
    ) -> Dict[str, int]:
        if not participants:
            return {"success": True, "added_count": 0, "skipped_count": 0}

        session_factory = get_session_factory()

        try:
            async with session_factory() as session:
                room = await session.get(Room, room_id)
                if not room:
                    logger.error(f"Room {room_id} not found")
                    return {"success": False, "added_count": 0, "skipped_count": 0}

                existing_participants = room.participants or []
                existing_identities: Set[str] = {
                    p.get("participant_identity")
                    for p in existing_participants
                    if p.get("participant_identity")
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

                    ts = p.get("timestamp") or datetime.now(timezone.utc)
                    if isinstance(ts, datetime):
                        ts = ts.isoformat()
                    
                    participants_to_add.append({
                        "participant_identity": identity,
                        "timestamp": ts,
                    })

                if participants_to_add:
                    added_count = len(participants_to_add)

                    update_stmt = (
                        update(Room)
                        .where(Room.id == room_id)
                        .values(
                            participants=Room.participants.concat(participants_to_add)
                        )
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
                    logger.info(f"ℹ️ No new participants to add to room {room_id}")
                    return {
                        "success": True,
                        "added_count": 0,
                        "skipped_count": skipped_count
                    }
        
        except Exception as e:
            logger.error(f"❌ Error in save_batch_participants: {e}")
            return {"success": False, "added_count": 0, "skipped_count": 0}

    async def update_room_participants(
        self, room_id: str, participants: List[Dict[str, Any]]
    ) -> bool:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                update_stmt = (
                    update(Room)
                    .where(Room.id == room_id)
                    .values(
                        participants=participants
                    )
                )
                await session.execute(update_stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update room participants: {e}")
            return False

    # ------------------------------------------------------------------
    # TRACKS
    # ------------------------------------------------------------------

    async def get_tracks_by_room(
        self, room_id: str, status: Optional[str] = None
    ) -> List[Track]:
        try:
            uid = str(uuid.UUID(str(room_id)))
        except (ValueError, AttributeError):
            logger.warning(f"get_tracks_by_room: invalid UUID format repr={repr(room_id)}, returning empty")
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

    async def get_track_by_id(self, track_id: str) -> Optional[Track]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                return await session.get(Track, track_id)
        except Exception as e:
            logger.error(f"Failed to get track: {e}")
            return None

    async def update_track_status(self, track_ref_id: str, status: str) -> dict:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(Track)
                    .where(Track.id == track_ref_id)
                    .values(
                        status=status,
                        updated_at=datetime.now(timezone.utc)
                    )
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

    async def check_event_record_done(self, room_ref_id: str) -> Optional[Room]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt_room = select(Room).where(
                    Room.id == room_ref_id,
                    Room.status == "final_room"
                )
                room = await session.scalar(stmt_room)

                if not room:
                    logger.info(
                        f"No room found for room_ref_id={room_ref_id} with status 'final_room'"
                    )
                    return None

                stmt_track = select(func.count()).select_from(Track).where(
                    Track.room_ref_id == room_ref_id,
                    Track.status == "pending"
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
                    .where(
                        Track.room_ref_id == room_ref_id,
                        Track.status.in_(["pending", "wait_process"])
                    )
                )
                pending_count = await session.scalar(stmt_count) or 0
                if pending_count > 0:
                    logger.debug(f"Room still has {pending_count} incomplete tracks")
                    return False

                stmt_update = (
                    update(Room)
                    .where(
                        Room.id == room_ref_id,
                        Room.status == "final_room"
                    )
                    .values(
                        status="completed",
                        completed_at=datetime.now(timezone.utc)
                    )
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
                    exists().where(
                        Room.id == room_id,
                        Room.participants.contains([{"participant_identity": user_id}])
                    )
                )
                has_access = await session.scalar(stmt)

                logger.debug(
                    f"user_has_room_access: user_id={user_id}, room_id={room_id}, has_access={has_access}"
                )

                return has_access

        except Exception as e:
            logger.error(f"Failed to check user room access: {e}")
            return False

    async def list_rooms_by_user(
        self,
        user_id: str,
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 10,
        skip: int = 0,
    ) -> List[Room]:
        session_factory = get_session_factory()
        stmt = select(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt,
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
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
        status: Optional[str] = None,
        search: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(Room)
        stmt = self._build_list_rooms_condition_query(
            stmt=stmt,
            status=status,
            search=search,
            from_utc=from_utc,
            to_utc=to_utc
        )
        stmt = stmt.where(Room.participants.contains([{"participant_identity": user_id}]))

        try:
            async with session_factory() as session:
                return await session.scalar(stmt) or 0
        except Exception as e:
            logger.error(f"Failed to count rooms by user: {e}")
            return 0

    # ------------------------------------------------------------------
    # SUMMARY
    # ------------------------------------------------------------------

    async def save_room_summary(self, summary_data: Dict[str, Any]) -> Optional[str]:
        session_factory = get_session_factory()
        room_uid = summary_data.get("room_id")
        try:
            async with session_factory() as session:
                new_summary = RoomSummary(
                    id=room_uid,
                    room_id=room_uid,
                    room_name=summary_data.get("room_name"),
                    participants=summary_data.get("participants", []),
                    summary_data=summary_data.get("summary_data", {}),
                    messages=summary_data.get("messages", []),
                    total_segments=summary_data.get("total_segments", 0),
                    created_at=summary_data.get("created_at", datetime.now(timezone.utc))
                )
                session.add(new_summary)
                await session.commit()
                return str(room_uid) if room_uid else None
        except Exception as e:
            logger.error(f"Failed to save room summary: {e}")
            return None

    async def update_room_summary(self, room_id: str, summary_data: Dict[str, Any]) -> bool:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(RoomSummary)
                    .where(RoomSummary.room_id == room_id)
                    .values(
                        summary_data=summary_data,
                    )
                    .returning(RoomSummary.id)
                )
                res = await session.execute(stmt)
                await session.commit()
                return res.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed to update summary: {e}")
            return False

    async def get_summary_by_room_id(self, room_id: str) -> Tuple[Optional[RoomSummary], Optional[Room]]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                room = await session.get(Room, room_id)
                if not room:
                    return None, None

                stmt = (
                    select(RoomSummary)
                    .where(RoomSummary.room_id == room_id)
                    .order_by(RoomSummary.created_at.desc())
                    .limit(1)
                )
                summary = await session.scalar(stmt)
                return summary, room
        except Exception as e:
            logger.error(f"Failed to get summary by id: {e}")
            return None, None

    async def get_summary_by_room_name(
        self,
        room_name: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[List[RoomSummary], List[Room]]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                # 1. build query for rooms
                room_stmt = select(Room).where(Room.room_name == room_name)
                if start_time:
                    room_stmt = room_stmt.where(Room.created_at >= start_time)
                if end_time:
                    room_stmt = room_stmt.where(Room.created_at <= end_time)
                if user_id:
                    room_stmt = room_stmt.where(Room.participants.contains([{"participant_identity": user_id}]))
                
                room_stmt = room_stmt.order_by(Room.created_at.desc())
                room_list = list((await session.scalars(room_stmt)).all())

                if not room_list:
                    return [], []

                room_ids = [r.id for r in room_list]

                # 2. get summaries
                summary_stmt = select(RoomSummary).where(RoomSummary.room_id.in_(room_ids))
                summary_list = list((await session.scalars(summary_stmt)).all())

                return summary_list, room_list
        except Exception as e:
            logger.error(f"Failed to get summary by room name: {e}")
            return [], []

    # ------------------------------------------------------------------
    # METADATA EVENTS
    # ------------------------------------------------------------------

    async def save_metadata_event(self, event_data: Dict[str, Any]) -> Optional[str]:
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
                    created_at=datetime.now(timezone.utc),
                )
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=[MetadataEvent.event_id]
                )
                await session.execute(stmt)
                await session.commit()
                return str(uid)
        except Exception as e:
            logger.error(f"Failed to save event: {e}")
            return None

    async def get_metadata_event_by_event_id(self, event_id: str) -> Optional[MetadataEvent]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = select(MetadataEvent).where(MetadataEvent.event_id == event_id)
                return await session.scalar(stmt)
        except Exception as e:
            logger.error(f"Failed to get event by id: {e}")
            return None

    async def append_transcript_chunk(
        self, track_ref_id: str, new_segments: List[Dict[str, Any]]
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
                start_index = (
                    (last_chunk_index + 1) if last_chunk_index is not None else 0
                )

                # Assume self._split_into_chunks exists or just dump everything if not available
                # Actually, Mongo implementation has self._split_into_chunks and self._create_chunk_document.
                # I'll replicate the logic or just insert them directly.
                # In Mongo, chunks are split. We'll implement a basic splitter here if needed, or just insert as one chunk for now.
                # To match exactly:
                chunk_size = 50
                chunks = [
                    new_segments[i : i + chunk_size]
                    for i in range(0, len(new_segments), chunk_size)
                ]

                chunk_documents = []
                for i, chunk_segments in enumerate(chunks):
                    start_time = (
                        chunk_segments[0].get("start", 0.0) if chunk_segments else 0.0
                    )
                    end_time = (
                        chunk_segments[-1].get("end", 0.0) if chunk_segments else 0.0
                    )
                    new_chunk = TranscriptChunk(
                        id=uuid.uuid4(),
                        track_ref_id=track_ref_id,
                        chunk_index=start_index + i,
                        start_time=start_time,
                        end_time=end_time,
                        item_count=len(chunk_segments),
                        segments=chunk_segments
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
        stmt: Select,
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> Select:
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
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
        limit: int = 100,
        skip: int = 0,
        sort_order: str = "desc",
    ) -> List[MetadataEvent]:
        session_factory = get_session_factory()
        stmt = select(MetadataEvent)
        stmt = self._build_metadata_events_condition_query(
            stmt=stmt,
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc
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
        event_type: Optional[str] = None,
        room_id: Optional[str] = None,
        from_utc: Optional[datetime] = None,
        to_utc: Optional[datetime] = None,
    ) -> int:
        session_factory = get_session_factory()
        stmt = select(func.count()).select_from(MetadataEvent)
        stmt = self._build_metadata_events_condition_query(
            stmt=stmt,
            event_type=event_type,
            room_id=room_id,
            from_utc=from_utc,
            to_utc=to_utc
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
        limit: int = None,
        skip: int = 0,
    ) -> List[TranscriptChunk]:
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
        track_ids: List[str],
        sorted_by_index: bool = True,
    ) -> List[TranscriptChunk]:
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
        egress_id: str = None,
        track_id: Optional[str] = None,
        room_ref_id: Optional[str] = None,
        participant_identity: Optional[str] = None,
        audio_info: Optional[Dict[str, Any]] = None,
        status: str = "pending",
        error: Optional[str] = None,
    ) -> Optional[Track]:
        if not egress_id:
            logger.error("egress_id is required")
            return None

        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)

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
                        updated_at=now
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
