import gzip
import logging
import os
import shutil
import sys
import threading
import glob
import warnings
from datetime import datetime
from logging.handlers import RotatingFileHandler
from concurrent.futures import ThreadPoolExecutor

from config.application_config import get_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configuration from application_config
config = get_config()
log_level = getattr(logging, config.logger.level, logging.INFO)
rotation_max_bytes = config.logger.rotation_max_mb * 1024 * 1024

# Backup count: Prevents infinite disk usage. 
backup_count = getattr(config.logger, 'backup_count', 5)

# --- SOLUTION FOR ISSUE 3: Safe Internal Fallback Logger ---
fallback_logger = logging.getLogger("GzipRotatingFileHandler.fallback")
fallback_logger.propagate = False
if not fallback_logger.handlers:
    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s | INTERNAL_LOG_ERROR | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    fallback_logger.addHandler(console_handler)


class GzipRotatingFileHandler(RotatingFileHandler):
    """
    Extended RotatingFileHandler utilizing asynchronous compression via a shared thread pool:
    - Current active file: {service}.log
    - Rotated files are quickly renamed synchronously, releasing the logging lock instantly.
    - Compression is offloaded to a shared single-threaded ThreadPoolExecutor to prevent CPU/IO spikes.
    - Old compressed logs (.gz) and failed fallback logs (.err) are managed with sequential numbering (1..N).
    """
    
    # Shared single-threaded executor for all instances to serialize heavy I/O tasks
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LogCompressor")

    def __init__(self, *args, **kwargs):
        # Extract the actual backup count from args or kwargs safely
        if len(args) >= 4:
            self.real_backup_count = args[3]
            args = list(args)
            args[3] = 1
            args = tuple(args)
        else:
            self.real_backup_count = kwargs.get("backupCount", backup_count)
            kwargs["backupCount"] = 1

        super().__init__(*args, **kwargs)
        
        # Thread-safe lock to protect shifting and renaming
        self._lock_reserved = threading.Lock()

    def rotation_filename(self, default_name):
        """Override: Return a placeholder name. Real naming is managed in rotate()."""
        return f"{default_name}.tmp"

    def rotate(self, source, dest):
        """Override: Quickly rename the log file synchronously and delegate shifting/compression to the background task."""
        if not os.path.exists(source):
            return

        # Generate a highly unique temporary name using a microsecond timestamp
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S_%f")
        temp_source = f"{source}.{timestamp}.tmp"
        
        try:
            # Synchronous rename is extremely fast, instantly freeing the active log file
            os.rename(source, temp_source)
        except Exception as e:
            fallback_logger.error(f"Failed to rename active log file {source} to {temp_source}: {e}")
            raise e

        # Submit the compression and shifting task to the shared single-threaded thread pool
        self._executor.submit(self._compress_and_shift_task, temp_source)

    def _compress_and_shift_task(self, temp_source):
        """Compress the temporary log file and shift previous backups sequentially in the background."""
        if self.real_backup_count <= 0:
            if os.path.exists(temp_source):
                try:
                    os.remove(temp_source)
                except OSError:
                    pass
            return

        temp_gzip = f"{temp_source}.gz"
        compression_success = False
        
        try:
            with open(temp_source, "rb") as f_in:
                with gzip.open(temp_gzip, "wb") as f_out:
                    shutil.copyfileobj(f_in, f_out)
            compression_success = True
        except Exception as compress_error:
            fallback_logger.error(
                f"Compression failed for {temp_source}. Error: {compress_error}"
            )

        with self._lock_reserved:
            try:
                # 1. Determine current sequential backups on disk (1..K)
                current_k = 0
                for i in range(1, self.real_backup_count + 1):
                    gz_path = f"{self.baseFilename}.{i}.gz"
                    err_path = f"{self.baseFilename}.{i}.err"
                    if os.path.exists(gz_path) or os.path.exists(err_path):
                        current_k = i
                    else:
                        break

                if current_k < self.real_backup_count:
                    # Space available: write to the next logical slot
                    target_slot = current_k + 1
                else:
                    # Capacity reached: shift slot 2->1, 3->2, etc. and free the last slot
                    target_slot = self.real_backup_count
                    
                    # Remove the oldest backup files (slot 1)
                    for ext in (".gz", ".err"):
                        oldest = f"{self.baseFilename}.1{ext}"
                        if os.path.exists(oldest):
                            try:
                                os.remove(oldest)
                            except OSError as e:
                                warnings.warn(f"Failed to delete oldest log {oldest}: {e}")

                    # Shift existing backups down by one slot
                    for i in range(2, self.real_backup_count + 1):
                        for ext in (".gz", ".err"):
                            src = f"{self.baseFilename}.{i}{ext}"
                            dst = f"{self.baseFilename}.{i-1}{ext}"
                            if os.path.exists(src):
                                try:
                                    os.replace(src, dst)
                                except OSError as e:
                                    fallback_logger.error(f"Failed to shift {src} to {dst}: {e}")

                # 2. Place the new file in the target slot
                if compression_success:
                    final_dest = f"{self.baseFilename}.{target_slot}.gz"
                    if os.path.exists(temp_gzip):
                        os.rename(temp_gzip, final_dest)
                    if os.path.exists(temp_source):
                        try:
                            os.remove(temp_source)
                        except OSError:
                            pass
                else:
                    # Save uncompressed file as fallback .err
                    final_dest = f"{self.baseFilename}.{target_slot}.err"
                    if os.path.exists(temp_source):
                        os.rename(temp_source, final_dest)
                    if os.path.exists(temp_gzip):
                        try:
                            os.remove(temp_gzip)
                        except OSError:
                            pass

            except Exception as shift_error:
                fallback_logger.error(f"Error during shifting/renaming backups: {shift_error}")
                # Safe cleanup of temporary files on error
                for path in (temp_source, temp_gzip):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            finally:
                # Clean up legacy date-formatted files and enforce bounds
                self._cleanup_old_logs()

    def _cleanup_old_logs(self):
        """Remove legacy pattern files or out-of-bounds backups."""
        try:
            log_dir = os.path.dirname(self.baseFilename)
            base_name = os.path.basename(self.baseFilename)
            for entry in os.listdir(log_dir):
                full_path = os.path.join(log_dir, entry)
                if not os.path.isfile(full_path):
                    continue
                if not entry.startswith(base_name + "."):
                    continue
                
                if entry.endswith(".gz") or entry.endswith(".err"):
                    parts = entry.rsplit(".", 2)
                    if len(parts) >= 3:
                        num_str = parts[-2]
                        try:
                            num = int(num_str)
                            if num > self.real_backup_count or num <= 0:
                                os.remove(full_path)
                        except ValueError:
                            # Remove non-integer suffix files (legacy date format, e.g. YYYYMMDD)
                            os.remove(full_path)
        except Exception as e:
            fallback_logger.error(f"Error during safety-net cleanup: {e}")


# Cache dictionary to safely share one file lock across multiple loggers
_HANDLERS = {}
_HANDLERS_LOCK = threading.Lock()

def _get_or_create_file_handler(service_name: str) -> GzipRotatingFileHandler:
    """Create or retrieve existing file handler to prevent duplicate locks on one file."""
    with _HANDLERS_LOCK:
        if service_name in _HANDLERS:
            return _HANDLERS[service_name]

        log_file = os.path.join(LOG_DIR, f"{service_name}.log")
        handler = GzipRotatingFileHandler(log_file, maxBytes=rotation_max_bytes)
        
        formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        handler.setLevel(log_level)
        
        _HANDLERS[service_name] = handler
        return handler


def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    if not logger.handlers:
        logger.propagate = False
        service_name = "agents_service"
        file_handler = _get_or_create_file_handler(service_name)
        logger.addHandler(file_handler)

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