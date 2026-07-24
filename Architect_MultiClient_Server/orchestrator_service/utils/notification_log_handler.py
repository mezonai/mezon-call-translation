import atexit
import logging
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

# Module-level thread pool — shared across all NotificationHandler instances
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="notification")
atexit.register(_executor.shutdown, wait=False)

# Lazy-init sync HTTP client (created once on first use, shared across threads)
_http_client: httpx.Client | None = None


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client(timeout=30.0, follow_redirects=True)
    return _http_client


def _send_webhook(title: str, message: dict[str, Any]) -> None:  # type: ignore[explicit-any]
    """
    Send notification via HTTP webhook (runs in a thread pool worker).

    This function is submitted to ThreadPoolExecutor and runs synchronously
    in a background OS thread — does not block the asyncio event loop.
    """
    try:
        config = get_config().notification

        channel_id = config.channel_id
        webhook_token = config.webhook_token

        if not channel_id or not webhook_token:
            logger.warning("❌ Missing notification config: channel_id or webhook_token not configured")
            return

        webhook_url = f"{config.webhook_endpoint}/{channel_id}/{webhook_token}"

        # TODO: Use `Any` type because notification message payload can have dynamic structures
        payload: dict[str, Any] = {  # type: ignore[explicit-any]
            "type": "hook",
            "message": message,
        }

        client = _get_http_client()
        response = client.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
        )

        if response.status_code in [200, 201, 202, 204]:
            logger.info(f"✅ Webhook sent successfully: {response.status_code}")
        else:
            logger.warning(f"⚠️ Webhook returned status {response.status_code}: {response.text}")
    except Exception as e:
        logger.warning(f"❌ Error sending webhook: {e}", exc_info=True)


class NotificationHandler(logging.Handler):
    """
    Automatically send ERROR logs to notification system.

    Uses a ThreadPoolExecutor to submit webhook tasks to background OS threads.
    Completely independent of Redis and asyncio event loop.
    """

    def __init__(self):
        super().__init__()

        self._cooldown_sec = 60
        self._last_sent: dict[str, float] = {}

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

            # Submit to thread pool — non-blocking, runs in background OS thread
            title = f"🚫 {record.levelname} in {record.name}"

            _executor.submit(
                _send_webhook,
                title,
                {
                    "t": f"{title}{message}",
                    "mk": [{"type": "pre", "s": len(title) + 1, "e": len(title) + len(message) + 1}],
                },
            )

        except Exception:
            pass
