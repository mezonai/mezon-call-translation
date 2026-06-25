import contextlib
import logging
import time
import traceback
from typing import Any

from orchestrator_service.utils.asyncio_task_manager import asyncio_create_task_safety


class NotificationHandler(logging.Handler):
    """
    Automatically send ERROR logs to notification system.
    """

    def __init__(self):
        super().__init__()

        self._cooldown_sec = 60
        self._last_sent: dict[str, float] = {}

        self._producer: Any | None = None
        self._connected: bool = False

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

    async def _ensure_connected(self):
        if self._producer is None:
            from orchestrator_service.services.notification_producer import (
                NotificationProducerService,
            )

            self._producer = NotificationProducerService()

        if not self._connected:
            await self._producer.connect()
            self._connected = True

    async def _send_notification(
        self,
        record: logging.LogRecord,
        message: str,
    ):
        try:
            await self._ensure_connected()

            producer = self._producer
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
