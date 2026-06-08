import logging
from logging.handlers import SysLogHandler

from stt_service.config.app_config import get_config

# Configuration from app_config
config = get_config()

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


    try:
        # Syslog handler — forwards all application logs to rsyslog
        syslog_app_handler = SysLogHandler(address='/dev/log')
        syslog_app_formatter = logging.Formatter("stt_service[%(process)d]: %(name)s | %(levelname)s | %(message)s")
        syslog_app_handler.setFormatter(syslog_app_formatter)
        syslog_app_handler.setLevel(log_level)
        root_logger.addHandler(syslog_app_handler)

    except Exception as e:
        print(f"Cannot connect to rsyslog socket: {e}", flush=True)
