"""
PostgreSQL repository for Generic Outbox Tasks.
Handles creating, fetching, and updating asynchronous outbox tasks.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

from sqlalchemy import text
from orchestrator_service.services.postgresql.database import get_session_factory
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.postgresql.models import OutboxUseCase, OutboxStatus

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
        now = datetime.now(timezone.utc)
        configs = {"room_id": room_id, "retry_type": retry_type}
        configs_json = json.dumps(configs)

        try:
            async with session_factory() as session:
                # Check for existing pending task for same room_id and retry_type
                check_query = """
                    SELECT id FROM outbox_tasks
                    WHERE use_case = CAST(:use_case AS outboxusecase)
                      AND configs->>'room_id' = :room_id
                      AND configs->>'retry_type' = :retry_type
                      AND status = CAST(:status AS outboxstatus)
                    LIMIT 1
                """
                res = await session.execute(
                    text(check_query),
                    {
                        "use_case": OutboxUseCase.RETRY_SUMMARIZATION.value,
                        "room_id": room_id, 
                        "retry_type": retry_type,
                        "status": OutboxStatus.PENDING.value
                    }
                )
                existing = res.fetchone()

                if existing:
                    # Update existing pending task's error and timestamp
                    task_id = existing[0]
                    update_query = """
                        UPDATE outbox_tasks 
                        SET last_error = :error_msg,
                            updated_at = :now
                        WHERE id = :id
                    """
                    await session.execute(
                        text(update_query),
                        {
                            "id": task_id,
                            "error_msg": error_msg,
                            "now": now,
                        }
                    )
                else:
                    # Insert new outbox task
                    task_id = str(uuid.uuid4())
                    insert_query = """
                        INSERT INTO outbox_tasks
                            (id, use_case, status, configs, last_error, created_at, updated_at)
                        VALUES
                            (:id, CAST(:use_case AS outboxusecase), CAST(:status AS outboxstatus), CAST(:configs AS jsonb), :error_msg, :now, :now)
                    """
                    await session.execute(
                        text(insert_query),
                        {
                            "id": task_id,
                            "use_case": OutboxUseCase.RETRY_SUMMARIZATION.value,
                            "status": OutboxStatus.PENDING.value,
                            "configs": configs_json,
                            "error_msg": error_msg,
                            "now": now,
                        }
                    )
                await session.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to add task to outbox: {e}", exc_info=True)
            return False

    async def fetch_pending_outbox_tasks(self, limit: int = 5, use_case: Optional[str] = None) -> List[Dict[str, Any]]:
        session_factory = get_session_factory()

        query = """
            SELECT * FROM outbox_tasks
            WHERE status = CAST(:status AS outboxstatus)
        """
        params = {"limit": limit, "status": OutboxStatus.PENDING.value}

        if use_case:
            query += " AND use_case = CAST(:use_case AS outboxusecase)"
            params["use_case"] = use_case

        query += """
            ORDER BY created_at ASC 
            LIMIT :limit 
            FOR UPDATE SKIP LOCKED
        """

        try:
            async with session_factory() as session:
                res = await session.execute(
                    text(query),
                    params
                )
                rows = res.fetchall()
                tasks = []
                for row in rows:
                    t = dict(row._mapping)
                    t["id"] = str(t["id"])
                    tasks.append(t)
                return tasks
        except Exception as e:
            logger.error(f"Failed to fetch pending outbox tasks: {e}")
            return []

    async def update_outbox_task_status(
        self,
        task_id: str,
        status: str,
        error_msg: Optional[str] = None,
    ) -> bool:
        session_factory = get_session_factory()
        now = datetime.now(timezone.utc)

        query = "UPDATE outbox_tasks SET status = CAST(:status AS outboxstatus), updated_at = :now"
        params = {"id": task_id, "status": status, "now": now}

        if error_msg is not None:
            query += ", last_error = :error_msg"
            params["error_msg"] = error_msg

        query += " WHERE id = :id"

        try:
            async with session_factory() as session:
                await session.execute(text(query), params)
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