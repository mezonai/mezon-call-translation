"""
PostgreSQL repository for Generic Outbox Tasks.
Handles creating, fetching, and updating asynchronous outbox tasks.
"""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.services.postgresql.models import OutboxStatus, OutboxTask, OutboxUseCase
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class PgOutboxRepository:
    """PostgreSQL-backed outbox repository to handle async background tasks."""

    def __init__(self):
        pass

    async def add_retry_summarization_task_to_outbox(
        self,
        room_id: str,
        retry_type: str,
        error_msg: str,
    ) -> bool:
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        configs = {"room_id": room_id, "retry_type": retry_type}

        try:
            async with session_factory() as session:
                stmt = (
                    select(OutboxTask)
                    .where(OutboxTask.use_case == OutboxUseCase.RETRY_SUMMARIZATION)
                    .where(OutboxTask.status == OutboxStatus.PENDING)
                    .where(OutboxTask.configs["room_id"] == room_id)
                    .where(OutboxTask.configs["retry_type"] == retry_type)
                    .limit(1)
                )
                res = await session.execute(stmt)
                existing_task = res.scalar_one_or_none()

                if existing_task:
                    existing_task.last_error = error_msg
                    existing_task.updated_at = now
                else:
                    new_task = OutboxTask(
                        id=uuid.uuid4(),
                        use_case=OutboxUseCase.RETRY_SUMMARIZATION,
                        status=OutboxStatus.PENDING,
                        configs=configs,
                        last_error=error_msg,
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(new_task)

                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add task to outbox: {e}", exc_info=True)
            return False

    async def fetch_pending_outbox_tasks(self, limit: int = 5, use_case: str | None = None) -> list[OutboxTask]:
        session_factory = get_session_factory()
        try:
            async with session_factory() as session:
                stmt = (
                    select(OutboxTask)
                    .where(OutboxTask.status == OutboxStatus.PENDING)
                    .order_by(OutboxTask.created_at.asc())
                    .limit(limit)
                    .with_for_update(skip_locked=True)
                )

                if use_case:
                    stmt = stmt.where(OutboxTask.use_case == use_case)

                orm_tasks = list((await session.scalars(stmt)).all())
                return orm_tasks
        except Exception as e:
            logger.error(f"Failed to fetch pending outbox tasks: {e}")
            return []

    async def update_outbox_task_status(
        self,
        task_id: str,
        status: str,
        error_msg: str | None = None,
    ) -> bool:
        session_factory = get_session_factory()
        now = datetime.now(UTC)
        try:
            async with session_factory() as session:
                update_values = {"status": status, "updated_at": now}
                if error_msg is not None:
                    update_values["last_error"] = error_msg

                stmt = update(OutboxTask).where(OutboxTask.id == task_id).values(**update_values)
                await session.execute(stmt)
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to update outbox task {task_id}: {e}")
            return False


_pg_outbox_repository: PgOutboxRepository | None = None


def get_pg_outbox_repository() -> PgOutboxRepository:
    global _pg_outbox_repository
    if _pg_outbox_repository is None:
        _pg_outbox_repository = PgOutboxRepository()
    return _pg_outbox_repository
