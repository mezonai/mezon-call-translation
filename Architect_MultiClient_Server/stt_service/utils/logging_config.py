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

from stt_service.config.app_config import get_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # stt_service dir
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# Configuration from app_config
config = get_config()
rotation_max_bytes = getattr(config.logging, 'max_file_size', 10 * 1024 * 1024)
backup_count = getattr(config.logging, 'backup_count', 30)

# Fallback logger
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
    Extended RotatingFileHandler utilizing asynchronous compression via a shared thread pool.
    """
    _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="LogCompressor")

    def __init__(self, *args, **kwargs):
        if len(args) >= 4:
            self.real_backup_count = args[3]
            args = list(args)
            args[3] = 1
            args = tuple(args)
        else:
            self.real_backup_count = kwargs.get("backupCount", backup_count)
            kwargs["backupCount"] = 1
        super().__init__(*args, **kwargs)
        self._lock_reserved = threading.Lock()

    def rotation_filename(self, default_name):
        return f"{default_name}.tmp"

    def rotate(self, source, dest):
        if not os.path.exists(source):
            return
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S_%f")
        temp_source = f"{source}.{timestamp}.tmp"
        try:
            os.rename(source, temp_source)
        except Exception as e:
            fallback_logger.error(f"Failed to rename active log file {source} to {temp_source}: {e}")
            raise e
        self._executor.submit(self._compress_and_shift_task, temp_source)

    def _compress_and_shift_task(self, temp_source):
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
            fallback_logger.error(f"Compression failed for {temp_source}. Error: {compress_error}")

        with self._lock_reserved:
            try:
                current_k = 0
                for i in range(1, self.real_backup_count + 1):
                    gz_path = f"{self.baseFilename}.{i}.gz"
                    err_path = f"{self.baseFilename}.{i}.err"
                    if os.path.exists(gz_path) or os.path.exists(err_path):
                        current_k = i
                    else:
                        break

                if current_k < self.real_backup_count:
                    target_slot = current_k + 1
                else:
                    target_slot = self.real_backup_count
                    for ext in (".gz", ".err"):
                        oldest = f"{self.baseFilename}.1{ext}"
                        if os.path.exists(oldest):
                            try:
                                os.remove(oldest)
                            except OSError as e:
                                warnings.warn(f"Failed to delete oldest log {oldest}: {e}")

                    for i in range(2, self.real_backup_count + 1):
                        for ext in (".gz", ".err"):
                            src = f"{self.baseFilename}.{i}{ext}"
                            dst = f"{self.baseFilename}.{i-1}{ext}"
                            if os.path.exists(src):
                                try:
                                    os.replace(src, dst)
                                except OSError as e:
                                    fallback_logger.error(f"Failed to shift {src} to {dst}: {e}")

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
                for path in (temp_source, temp_gzip):
                    if os.path.exists(path):
                        try:
                            os.remove(path)
                        except OSError:
                            pass
            finally:
                self._cleanup_old_logs()

    def _cleanup_old_logs(self):
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
                            os.remove(full_path)
        except Exception as e:
            fallback_logger.error(f"Error during safety-net cleanup: {e}")

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

    # File handler cho logging thường
    app_log_file = os.path.join(LOG_DIR, 'stt_service.log')
    app_handler = GzipRotatingFileHandler(app_log_file, maxBytes=rotation_max_bytes, encoding='utf-8')
    app_handler.setLevel(log_level)
    app_handler.setFormatter(formatter)
    
    # Chỉ lấy các log không phải metrics
    class NoMetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" not in record.getMessage()
    
    app_handler.addFilter(NoMetricsFilter())
    root_logger.addHandler(app_handler)

    # File handler cho metrics
    metrics_log_file = os.path.join(LOG_DIR, 'metrics.log')
    metrics_handler = GzipRotatingFileHandler(metrics_log_file, maxBytes=rotation_max_bytes, encoding='utf-8')
    metrics_handler.setLevel(log_level)
    metrics_handler.setFormatter(formatter)
    
    # Chỉ lấy các log metrics
    class MetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" in record.getMessage()
    
    metrics_handler.addFilter(MetricsFilter())
    root_logger.addHandler(metrics_handler)
