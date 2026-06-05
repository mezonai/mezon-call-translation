import logging
import os
import sys
from logging.handlers import SysLogHandler

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.notification_log_handler import NotificationHandler

# Load config
_cfg = get_config().logger
log_level = getattr(logging, _cfg.level, logging.INFO)
notification_level = getattr(logging, getattr(_cfg, 'notification_level', 'ERROR'), logging.ERROR)

# --- SAFE INTERNAL FALLBACK LOGGER ---
fallback_logger = logging.getLogger("SysLogHandler.fallback")
fallback_logger.propagate = False
if not fallback_logger.handlers:
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s | INTERNAL_LOG_ERROR | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    fallback_logger.addHandler(console_handler)

def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.propagate = False
        service_name = "orchestrator_service"
        
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

        # Notification handler: send alerts when log level >= notification_level
        notification_handler = NotificationHandler()
        notification_handler.setLevel(notification_level)
        notification_handler.setFormatter(formatter)
        logger.addHandler(notification_handler)

        try:
            syslog_handler = SysLogHandler(address='/dev/log')
            syslog_formatter = logging.Formatter(f"{service_name}[%(process)d]: %(name)s | %(levelname)s | %(message)s")
            syslog_handler.setFormatter(syslog_formatter)
            syslog_handler.setLevel(log_level)
            logger.addHandler(syslog_handler)
        except Exception as e:
            fallback_logger.error(f"Cannot connect to rsyslog socket: {e}")

    logger.setLevel(log_level)
    return logger

# Suppress noisy log messages from third-party libraries
logging.getLogger("websockets").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

def get_logger(name: str) -> logging.Logger:
    """Utility function to get a logger with consistent formatting"""
    return setup_logger(name)

# Set up the base logger
logger = get_logger(__name__)