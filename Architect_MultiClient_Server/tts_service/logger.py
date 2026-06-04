import gzip
import logging
import os
import shutil
from datetime import datetime
from logging.handlers import RotatingFileHandler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Config from environment variables
log_level = getattr(logging, os.getenv('LOG_LEVEL', 'INFO').upper(), logging.INFO)
rotation_max_bytes = int(os.getenv('LOG_ROTATION_MAX_MB', '500')) * 1024 * 1024


class GzipRotatingFileHandler(RotatingFileHandler):
    """
    Extended RotatingFileHandler:
    - Current active file: {service}.log
    - When exceeding rotation_max_bytes, the old file is compressed to:
        {service}.log.{YYYYMMDD}.gz
    - If multiple rotations occur on the same day: {service}.log.{YYYYMMDD}_1.gz, ...
    """

    def doRollover(self):
        """Override: close the current file, and compress it into .gz with a daily timestamp."""
        if self.stream:
            self.stream.close()
            self.stream = None

        # Construct gz file path: {service}.log.YYYYMMDD.gz
        date_str = datetime.now().strftime("%Y%m%d")
        gz_path = f"{self.baseFilename}.{date_str}.gz"

        # Avoid overwriting if a rotation already occurred on the same day
        counter = 1
        while os.path.exists(gz_path):
            gz_path = f"{self.baseFilename}.{date_str}_{counter}.gz"
            counter += 1

        # Compress the current log file -> .gz
        if os.path.exists(self.baseFilename):
            with open(self.baseFilename, "rb") as f_in:
                with gzip.open(gz_path, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            os.remove(self.baseFilename)

        # Reopen a new empty log file
        self.mode = "a"
        self.stream = self._open()


def _make_file_handler(service_name: str) -> GzipRotatingFileHandler:
    """Create file handler: logs/{service_name}.log, rotate -> {service_name}.log.YYYYMMDD.gz"""
    log_file = os.path.join(LOG_DIR, f"{service_name}.log")
    handler = GzipRotatingFileHandler(
        log_file,
        maxBytes=rotation_max_bytes,
        backupCount=0,  # Managed by custom gz naming scheme
    )
    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    handler.setFormatter(formatter)
    handler.setLevel(log_level)
    return handler


def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    # Only configure logger if it hasn't been configured yet
    if not logger.handlers:
        # Prevent propagation to the root logger to avoid duplicate or unintended logs
        logger.propagate = False

        # File handler: logs/{service_name}.log (rotate -> .log.YYYYMMDD.gz)
        # Use the prefix of the logger name as the service name
        # e.g., "tts_service.core" -> "tts_service"
        service_name = name.split(".")[0] if "." in name else name
        file_handler = _make_file_handler(service_name)
        logger.addHandler(file_handler)

    # Set level
    logger.setLevel(log_level)

    return logger


# Set up base logger
logger = setup_logger(__name__)

# Suppress noisy loggers
logging.getLogger('websockets').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# Set up metrics logger
metrics_logger = setup_logger('metrics')

def get_logger(name: str) -> logging.Logger:
    """Get logger with consistent formatting"""
    return setup_logger(name)