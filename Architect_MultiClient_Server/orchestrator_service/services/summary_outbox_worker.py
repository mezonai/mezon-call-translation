"""
Summary Outbox Worker Service - Processes and retries failed summarization tasks.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, ClassVar

from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.summary_models import RetryType
from orchestrator_service.services.postgresql.models import OutboxStatus
from orchestrator_service.services.postgresql.pg_outbox_repository import PgOutboxRepository
from orchestrator_service.services.summary_service import get_summary_service
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

# Task Handler type definition
TaskHandler = Callable[[dict[str, Any]], Awaitable[None]]  # type: ignore[explicit-any]


class OutboxHandlerRegistry:
    """Registry for mapping use_case to task handlers."""

    _registry: ClassVar[dict[str, TaskHandler]] = {}

    @classmethod
    def register(cls, use_case: str, handler: TaskHandler) -> None:
        cls._registry[use_case] = handler
        logger.info(f"Registered outbox task handler for use_case: {use_case}")

    @classmethod
    def get_handler(cls, use_case: str) -> TaskHandler | None:
        return cls._registry.get(use_case)


# ------------------------------------------------------------------
# Task Handlers
# ------------------------------------------------------------------


async def handle_retry_summarization(configs: dict[str, Any]) -> None:  # type: ignore[explicit-any]
    room_id = configs.get("room_id")
    retry_type_str = configs.get("retry_type")
    if not room_id or not retry_type_str:
        raise ValueError("Missing room_id or retry_type in outbox task configs")

    # Map the config retry_type string to the RetryType Enum
    retry_type = RetryType(retry_type_str)

    logger.info(f"Retrying summarization for room {room_id} with type {retry_type.value}")
    summary_data = await get_summary_service().retry_summary_from_full_text(room_id=room_id, retry_type=retry_type)
    if not summary_data:
        raise RuntimeError("Failed to generate summary or action items via retry")


# Register default handler
OutboxHandlerRegistry.register("retry_summarization", handle_retry_summarization)

async def handle_retry_transcript_correction(configs: dict[str, Any]) -> None:  # type: ignore[explicit-any]
    from orchestrator_service.models.transcript_models import TranscriptCorrectionRetryType
    from orchestrator_service.services.transcript_correction_service import get_correction_service

    room_id = configs.get("room_id")
    if not room_id:
        raise ValueError("Missing room_id in outbox task configs")

    retry_type_str = configs.get("retry_type")
    if not retry_type_str:
        raise ValueError("Missing retry_type in outbox task configs")

    retry_type = TranscriptCorrectionRetryType(retry_type_str)

    logger.info(f"Retrying transcript correction for room {room_id} with type {retry_type.value}")
    await get_correction_service().correct_transcript_for_room(room_id, retry_type=retry_type)

OutboxHandlerRegistry.register("retry_transcript_correction", handle_retry_transcript_correction)


@singleton
class SummaryOutboxWorker:
    """Background worker that polls and processes pending outbox tasks at scheduled hours."""

    def __init__(self):
        outbox_cfg = get_config().outbox
        self.outbox_repo = PgOutboxRepository()
        self._running = False
        self._worker_task: asyncio.Task[None] | None = None
        self._last_run_hour = -1  # Prevent duplicate runs in the same hour
        self._check_interval_sec = outbox_cfg.check_interval_sec
        self._delay_between_items = outbox_cfg.delay_between_items_sec
        self._batch_limit = outbox_cfg.batch_limit
        self._target_hours = outbox_cfg.retry_summarization_target_hours
        logger.info("SummaryOutboxWorker initialized")

    async def start(self) -> None:
        """Start the background outbox processing worker."""
        if self._running:
            logger.warning("SummaryOutboxWorker is already running")
            return

        self._running = True
        self._worker_task = asyncio.create_task(self._worker_loop(), name="summary-outbox-worker-loop")
        logger.info("✅ SummaryOutboxWorker started")

    async def stop(self) -> None:
        """Stop the worker gracefully."""
        logger.info("Stopping SummaryOutboxWorker...")
        self._running = False

        if self._worker_task:
            self._worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker_task

        logger.info("✅ SummaryOutboxWorker stopped")

    async def _worker_loop(self) -> None:
        """Main loop that checks for target hours and processes tasks."""
        logger.info("🚀 SummaryOutboxWorker scheduler loop started")

        try:
            while self._running:
                try:
                    now = datetime.now(UTC)
                    # Trigger the job strictly at 19:00, 20:00, and 21:00
                    if now.hour in self._target_hours and now.hour != self._last_run_hour:
                        self._last_run_hour = now.hour
                        logger.info(f"Target hour reached ({now.hour}:00). Starting Outbox processing...")
                        await self._process_batch()
                except Exception as e:
                    logger.error(f"Error in outbox scheduler loop: {e}", exc_info=True)

                # Sleep before checking again
                await asyncio.sleep(self._check_interval_sec)
        except asyncio.CancelledError:
            logger.info("SummaryOutboxWorker scheduler loop cancelled")
        finally:
            logger.info("🛑 SummaryOutboxWorker scheduler loop stopped")

    async def _process_batch(self) -> None:
        """Fetch and process up to 5 pending tasks sorted by oldest first."""
        tasks = await self.outbox_repo.fetch_pending_outbox_tasks(
            limit=self._batch_limit
        )
        if not tasks:
            logger.info("No pending outbox tasks found to process.")
            return

        logger.info(f"Processing {len(tasks)} pending outbox tasks...")

        for index, task in enumerate(tasks):
            task_id = task.id
            use_case = task.use_case
            configs = task.configs

            handler = OutboxHandlerRegistry.get_handler(use_case)
            if not handler:
                logger.error(f"No handler registered for outbox use_case: {use_case}")
                await self.outbox_repo.update_outbox_task_status(
                    task_id=str(task_id),
                    status=OutboxStatus.FAILED,
                    error_msg=f"No handler registered for use_case: {use_case}",
                )
                continue

            try:
                # Mark as processing
                await self.outbox_repo.update_outbox_task_status(task_id=str(task_id), status=OutboxStatus.PROCESSING)

                # Execute handler
                await handler(configs)

                # Mark as completed on success
                await self.outbox_repo.update_outbox_task_status(task_id=str(task_id), status=OutboxStatus.COMPLETED)
                logger.info(f"✅ Outbox task {task_id} completed successfully")

            except Exception as e:
                # Task fails completely and is marked as failed on the first attempt
                await self.outbox_repo.update_outbox_task_status(
                    task_id=str(task_id), status=OutboxStatus.FAILED, error_msg=str(e)
                )
                logger.error(f"❌ Outbox task {task_id} failed and marked as 'failed': {e}", exc_info=True)

            # Rest 30 seconds between items (except for the last item in the batch)
            if index < len(tasks) - 1:
                logger.info(f"Sleeping for {self._delay_between_items} seconds to prevent LLM rate limiting...")
                await asyncio.sleep(self._delay_between_items)
