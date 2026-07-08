import contextlib
import logging
import time
import traceback
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator_service.services.notification_producer import NotificationProducerService

from orchestrator_service.utils.asyncio_task_manager import asyncio_create_task_safety


class NotificationHandler(logging.Handler):
    """
    Automatically send ERROR logs to notification system.
    """

    def __init__(self):
        super().__init__()

        self._cooldown_sec = 60
        self._last_sent: dict[str, float] = {}

        self._producer: NotificationProducerService | None = None

    def emit(self, record: logging.LogRecord):
        try:
            # Only ERROR+
            if record.levelno < logging.ERROR:
                return

            # Prevent recursive notification loop
            if record.name.startswith("orchestrator_service.utils.notification_log_handler"):
                return

            if record.name.startswith("orchestrator_service.services.notification"):
                return

            message = self.format(record)

            # Append traceback
            if record.exc_info:
                message += "\n\n"
                message += "".join(traceback.format_exception(*record.exc_info))

            # Anti spam
            error_key = f"{record.name}:{record.getMessage()}"

            now = time.time()
            last_sent = self._last_sent.get(error_key, 0.0)

            if now - last_sent < self._cooldown_sec:
                return

            self._last_sent[error_key] = now

            # Fire async task
            with contextlib.suppress(RuntimeError):
                asyncio_create_task_safety(self._send_notification(record, message))

        except Exception:
            pass

    def _get_producer(self) -> "NotificationProducerService | None":
        """
        Lazy-load producer to avoid circular import at module init time.

        Import chain without lazy loading:
            logger → notification_log_handler → notification_producer
            → redis/__init__ → base_hash_repository → logger  (circular!)
        """
        if self._producer is None:
            from orchestrator_service.services.notification_producer import (
                NotificationProducerService,
            )

            try:
                self._producer = NotificationProducerService()
            except RuntimeError:
                return None

        return self._producer

    async def _send_notification(
        self,
        record: logging.LogRecord,
        message: str,
    ):
        try:
            producer = self._get_producer()
            if not producer:
                return

            title = f"🚫 {record.levelname} in {record.name}"

            await producer.send(
                title=title,
                message={
                    "t": f"{title}{message}",
                    "mk": [{"type": "pre", "s": len(title) + 1, "e": len(title) + len(message) + 1}],
                },
            )
        except Exception:
            pass

