import logging
import os
import sys
from logging.handlers import SysLogHandler

from stt_service.config.app_config import get_config

# Configuration from app_config
config = get_config()

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

def setup_logging(level: int | None = None) -> None:
    """Configure root logging with a clear, uniform format.
    Safe to call multiple times; will only attach handlers once.
    """
    root_logger = logging.getLogger()

    if root_logger.handlers:
        # Already configured elsewhere
        if level is not None:
            root_logger.setLevel(level)
        return

    log_level = level or logging.INFO
    root_logger.setLevel(log_level)

    # Formatter cho tất cả handlers
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler (stdout)
    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Filters
    class NoMetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" not in record.getMessage()

    class MetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" in record.getMessage()

    try:
        # Handler cho log thường
        syslog_app_handler = SysLogHandler(address='/dev/log')
        syslog_app_formatter = logging.Formatter("stt_service[%(process)d]: %(name)s | %(levelname)s | %(message)s")
        syslog_app_handler.setFormatter(syslog_app_formatter)
        syslog_app_handler.setLevel(log_level)
        syslog_app_handler.addFilter(NoMetricsFilter())
        root_logger.addHandler(syslog_app_handler)

        # Handler cho log metrics
        syslog_metrics_handler = SysLogHandler(address='/dev/log')
        syslog_metrics_formatter = logging.Formatter("stt_metrics[%(process)d]: %(name)s | %(levelname)s | %(message)s")
        syslog_metrics_handler.setFormatter(syslog_metrics_formatter)
        syslog_metrics_handler.setLevel(log_level)
        syslog_metrics_handler.addFilter(MetricsFilter())
        root_logger.addHandler(syslog_metrics_handler)
        
    except Exception as e:
        fallback_logger.error(f"Cannot connect to rsyslog socket: {e}")
