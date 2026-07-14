import logging
import os
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.notification_log_handler import NotificationHandler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

SERVICE_NAME = "mezon-orchestrator-service"

log_level_str = get_config().logger.level
log_level = getattr(logging, log_level_str, logging.INFO)


def _namer(default_name: str) -> str:
    """
    Custom namer for TimedRotatingFileHandler.
    """
    dir_name = os.path.dirname(default_name)
    # Extract the date suffix added by the handler
    parts = default_name.rsplit(".", 1)
    if len(parts) == 2:
        date_str = parts[1]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            formatted = dt.strftime("%Y%m%d")
            return os.path.join(dir_name, f"{SERVICE_NAME}-{formatted}.log")
        except ValueError:
            pass
    return default_name


def _create_file_handler(log_file: str) -> TimedRotatingFileHandler:
    """Create a TimedRotatingFileHandler that rotates at 17:00."""
    handler = TimedRotatingFileHandler(
        filename=log_file,
        when="midnight",
        interval=1,
        backupCount=30,  # Keep 30 days of logs
        encoding="utf-8",
        utc=False,
    )
    handler.suffix = "%Y-%m-%d"  # suffix for the rotated file before renaming
    handler.namer = _namer
    return handler


def _create_shared_file_handler() -> TimedRotatingFileHandler:
    """
    Create a single shared file handler for all loggers.

    Avoids Windows PermissionError on rollover when multiple handlers
    try to rename the same file simultaneously.
    """
    service_log_path = os.path.join(LOG_DIR, f"{SERVICE_NAME}.log")
    handler = _create_file_handler(service_log_path)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    handler.setLevel(log_level)
    return handler


_shared_file_handler = _create_shared_file_handler()


def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    # Skip if our file handler is already attached (avoid duplicate handlers)
    has_our_handlers = any(isinstance(h, TimedRotatingFileHandler) for h in logger.handlers)

    if not has_our_handlers:
        # Clear any pre-existing handlers (e.g. uvicorn's default console handlers)
        logger.handlers.clear()

        logger.propagate = False

        # Notification handler
        notification_handler = NotificationHandler()
        notification_handler.setLevel(logging.ERROR)
        notification_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        logger.addHandler(notification_handler)

        # Shared file handler — all loggers write to the same service log file
        logger.addHandler(_shared_file_handler)

    # Set level
    logger.setLevel(log_level)

    return logger


# Set up base logger
logger = setup_logger(__name__)

# Suppress noisy loggers
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

# Set up metrics logger
metrics_logger = setup_logger("metrics")


class UvicornErrorFilter(logging.Filter):
    """
    Filter to rename 'uvicorn.error' logger name to 'uvicorn' in log records.
    This prevents confusing 'INFO' logs showing up as 'uvicorn.error'.
    """
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn.info"
        return True


# Redirect uvicorn loggers to service log file
for _uv_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _logger = setup_logger(_uv_name)
    if _uv_name == "uvicorn.error":
        _logger.addFilter(UvicornErrorFilter())


def get_logger(name: str) -> logging.Logger:
    """Get logger with consistent formatting"""
    return setup_logger(name)
