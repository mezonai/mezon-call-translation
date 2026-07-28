import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import Room, RoomSectionSummary, RoomSummary
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgSummaryRepository:
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
    # SUMMARY
    # ------------------------------------------------------------------

    async def save_room_summary(self, summary_data: dict[str, Any]) -> str | None:  # type: ignore[explicit-any]
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
                    created_at=summary_data.get("created_at", datetime.now(UTC)),
                )
                session.add(new_summary)
                await session.commit()
                return str(room_uid) if room_uid else None
        except Exception as e:
            logger.error(f"Failed to save room summary: {e}")
            return None

    async def update_room_summary(self, room_id: str, summary_data: dict[str, Any]) -> bool:  # type: ignore[explicit-any]
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

    async def get_summary_by_room_id(self, room_id: str) -> tuple[RoomSummary | None, Room | None]:
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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        user_id: str | None = None,
    ) -> tuple[list[RoomSummary], list[Room]]:
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
    # LIGHT SUMMARY
    # ------------------------------------------------------------------

    async def upsert_room_section_summary(self, record: dict[str, Any]) -> bool:  # type: ignore[explicit-any]
        session_factory = get_session_factory()

        try:
            room_uid = uuid.UUID(str(record["room_id"]))
            section_index = record["section_index"]
            summary_data = record.get("summary_data", {})

            async with session_factory() as session:
                stmt = insert(RoomSectionSummary).values(
                    id=record.get("id") or uuid.uuid4(),
                    room_id=room_uid,
                    room_name=record.get("room_name"),
                    section_index=section_index,
                    messages=record.get("messages", []),
                    summary_data=summary_data,
                    start_time=record.get("start_time"),
                    end_time=record.get("end_time"),
                )

                stmt = stmt.on_conflict_do_update(
                    index_elements=[RoomSectionSummary.room_id, RoomSectionSummary.section_index],
                    set_={
                        "room_name": record.get("room_name"),
                        "messages": record.get("messages", []),
                        "summary_data": summary_data,
                        "start_time": record.get("start_time"),
                        "end_time": record.get("end_time"),
                    },
                )

                await session.execute(stmt)
                await session.commit()
                return True

        except Exception as e:
            logger.error(f"Failed to upsert room section summary: {e}")
            return False

    async def get_section_summaries_by_room_id(self, room_id: str) -> list[RoomSectionSummary]:
        session_factory = get_session_factory()

        try:
            room_uid = uuid.UUID(str(room_id))

            async with session_factory() as session:
                stmt = (
                    select(RoomSectionSummary)
                    .where(RoomSectionSummary.room_id == room_uid)
                    .order_by(RoomSectionSummary.section_index.asc())
                )
                sections = list((await session.scalars(stmt)).all())

                return sections

        except Exception as e:
            logger.error(f"Failed to get section summaries by room id: {e}")
            return []

    async def update_room_summary_data(self, room_id: str, summary_data: dict[str, Any]) -> bool:  # type: ignore[explicit-any]
        return await self.update_room_summary(room_id, summary_data)

    async def delete_section_summaries_by_room_id(self, room_id: str) -> bool:
        session_factory = get_session_factory()

        try:
            room_uid = uuid.UUID(room_id)
            async with session_factory() as session:
                stmt = delete(RoomSectionSummary).where(RoomSectionSummary.room_id == room_uid)
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to delete section summaries for room {room_id}: {e}")
            return False


# --------------- Singleton ---------------
_pg_summary_repository: PgSummaryRepository | None = None


def get_pg_summary_repository() -> PgSummaryRepository:
    global _pg_summary_repository
    if _pg_summary_repository is None:
        _pg_summary_repository = PgSummaryRepository()
    return _pg_summary_repository
