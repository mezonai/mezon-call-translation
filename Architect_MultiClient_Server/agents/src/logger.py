import logging
from logging.handlers import SysLogHandler

from config.application_config import get_config

# Configuration from application_config
config = get_config()
log_level = getattr(logging, config.logger.level, logging.INFO)


def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.propagate = False
        service_name = "agents_service"
        
        try:
            syslog_handler = SysLogHandler(address='/dev/log')
            syslog_formatter = logging.Formatter(f"{service_name}[%(process)d]: %(name)s | %(levelname)s | %(message)s")
            syslog_handler.setFormatter(syslog_formatter)
            syslog_handler.setLevel(log_level)
            logger.addHandler(syslog_handler)
        except Exception as e:
            print(f"Cannot connect to rsyslog socket: {e}", flush=True)

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