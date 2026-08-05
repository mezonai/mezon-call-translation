"""
PostgreSQL repository for transcripts (replaces MongoDBService).
Handles rooms, tracks, chunks, summary, and metadata events.
"""

import json
import uuid
from datetime import UTC, datetime
from typing import Any, TypeVar

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
        self, room_id: str, participant_identity: str, timestamp: datetime | None = None, username: str | None = None
    ) -> bool:
        session_factory = get_session_factory()
        ts = timestamp or datetime.now(UTC)

        new_participant = [{"participant_identity": participant_identity, "username": username, "timestamp": ts.isoformat()}]

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

    # ------------------------------------------------------------------
    # Kept here deliberately (not moved to pg_track_repository.py):
    # mixes room state (rooms.record_notified_at/status) and track state
    # (tracks.derivative_status) in one atomic statement, so it doesn't
    # belong in a track-only repository. Placed at the very end of the
    # class instead of alongside the other room-completion checks above --
    # this file predates `develop`'s in-progress ORM migration for this
    # layer, so new methods land here (appended, not interleaved) to
    # minimize merge-conflict surface with that work (this hotfix branched
    # off `main`, not `develop` -- see audio-ingestion/PLAN.md D26).
    # ------------------------------------------------------------------
    async def check_and_notify_room_recordings_ready(self, room_ref_id: str) -> bool:
        """Room-level, fire-once gate for room_record_done (audio-ingestion PLAN.md D19).

        True only when this call is the one that flips record_notified_at from
        NULL -- i.e. the room is finalized AND every track in it has reached a
        terminal derivative_status. Must be called from both places order can
        arrive in (a track's derivative finishing, and room finalization)
        since either can happen first; the atomic UPDATE...WHERE guard (same
        technique as final_room_status()) ensures exactly one caller ever
        sees True for a given room, regardless of which order or how many
        times this is called concurrently.

        This NOT EXISTS check is blind to a track that has no row at all --
        relies on every track that starts recording eventually getting a row
        (created eagerly off `recording.started`, PLAN.md D26 --
        pg_track_repository.py::create_track_placeholder; previously rows
        only appeared at `recording.completed`/`.failed`, which let this
        fire before an in-flight track had ever been seen at all).

        KNOWN GAP, not yet handled anywhere (flag if you're looking at this
        because a room is stuck in status='final_room' with
        record_notified_at still NULL): if a track's row gets created by
        `recording.started` but record-service then crashes and loses its
        local durable state before it can ever deliver `recording.completed`/
        `.failed` for it (state_repo is local disk, PLAN.md D5 tier 3 -- an
        already-called-out loss scenario), that track's row sits at
        derivative_status='pending' forever and this NOT EXISTS never clears
        for its room. There is no timeout/reconciliation on the orchestrator
        side that force-terminates an abandoned track today -- would need
        something like "track pending longer than N minutes with no live
        record-service session -> mark derivative_status='failed'" before
        this can be called fully closed.
        """
        uid = room_ref_id
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                result = await session.execute(
                    text("""
                        UPDATE rooms SET record_notified_at = :now
                        WHERE id = :id AND record_notified_at IS NULL AND status = 'final_room'
                          AND NOT EXISTS (
                            SELECT 1 FROM tracks WHERE room_ref_id = :id
                            AND derivative_status NOT IN ('completed', 'failed')
                          )
                        RETURNING id
                    """),
                    {"id": uid, "now": datetime.now(UTC)},
                )
                await session.commit()
                return result.fetchone() is not None
        except Exception as e:
            logger.error(f"Failed check_and_notify_room_recordings_ready: {e}")
            return False

    # ------------------------------------------------------------------
    # Added as a new method rather than editing save_batch_participants in
    # place, for the same "minimize merge-conflict surface with develop's
    # ORM migration" reason as pg_track_repository.py -- appended at the
    # end of the file/class instead of interleaved with existing methods.
    # ------------------------------------------------------------------
    async def save_batch_participants_atomic(  # type: ignore[explicit-any]
        self, room_id: str, participants: list[dict[str, Any]]
    ) -> bool:
        """Race-safe replacement for save_batch_participants above.

        save_batch_participants has a real TOCTOU race: it SELECTs
        `participants` to compute a dedup set in Python, then does an
        unconditional `UPDATE ... SET participants = participants || :new`.
        The `||` on the right always reads the row's *live* value at UPDATE
        time, not the earlier SELECT -- so if a concurrent save_participant()
        call (webhook path, already atomic -- see its docstring/pattern)
        appends the same identity in between, the SELECT's dedup set won't
        know about it, and this UPDATE appends it again on top of the
        now-already-updated row. Result: a duplicated participant entry.
        Not hypothetical -- can happen today whenever a `participant_joined`
        webhook lands while /register's batch save (also awaiting a LiveKit
        API call) is still in flight for the same room.

        This version does the membership filter *inside* the same UPDATE
        Postgres evaluates under the row lock, so there is no separate read
        step for the dedup decision at all -- immune to the same race
        regardless of how much the batch save's surrounding code is
        reordered (e.g. moved onto a background task).
        """
        if not participants:
            return True

        candidates = []
        for p in participants:
            identity = p.get("participant_identity")
            username = p.get("username")
            if not identity:
                continue
            ts = p.get("timestamp") or datetime.now(UTC)
            if isinstance(ts, datetime):
                ts = ts.isoformat()
            candidates.append({"participant_identity": identity, "username": username, "timestamp": ts})

        if not candidates:
            return True

        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    update(Room)
                    .where(Room.id == room_id)
                    .values(
                        participants=text(
                            """
                            COALESCE(participants, '[]'::jsonb) || (
                                SELECT COALESCE(jsonb_agg(elem), '[]'::jsonb)
                                FROM jsonb_array_elements(CAST(:candidates AS jsonb)) AS elem
                                WHERE NOT (COALESCE(participants, '[]'::jsonb) @> jsonb_build_array(
                                    jsonb_build_object('participant_identity', elem->>'participant_identity')
                                ))
                            )
                            """
                        ).bindparams(candidates=json.dumps(candidates))
                    )
                    .returning(Room.id)
                )
                result = await session.execute(stmt)
                await session.commit()
                return result.scalar_one_or_none() is not None
        except Exception as e:
            logger.error(f"Failed to save participants batch (atomic): {e}")
            return False

# --------------- Singleton ---------------
_pg_transcript_repository: PgTranscriptRepository | None = None


def get_pg_transcript_repository() -> PgTranscriptRepository:
    global _pg_transcript_repository
    if _pg_transcript_repository is None:
        _pg_transcript_repository = PgTranscriptRepository()
    return _pg_transcript_repository
