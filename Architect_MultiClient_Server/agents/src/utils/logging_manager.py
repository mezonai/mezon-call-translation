import logging
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional
import threading
from logging.handlers import RotatingFileHandler

class StructuredLogFormatter(logging.Formatter):
    """Formats log records as structured JSON"""
    
    def __init__(self, app_name: str, **kwargs):
        super().__init__()
        self.app_name = app_name
        self.extra_fields = kwargs
    
    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON"""
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "app": self.app_name,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno
        }
        
        # Add extra fields
        log_data.update(self.extra_fields)
        
        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": str(record.exc_info[0].__name__),
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info)
            }
        
        # Add any extra attributes from record
        if hasattr(record, "extra"):
            log_data.update(record.extra)
        
        return json.dumps(log_data)

class LogManager:
    """Centralized logging manager"""
    
    _instance = None
    _lock = threading.Lock()
    
    def __init__(self, 
                 app_name: str,
                 log_dir: str = "logs",
                 max_size: int = 10_485_760,  # 10MB
                 backup_count: int = 5):
        self.app_name = app_name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        
        # Setup handlers
        self.handlers = {
            "app": self._create_handler(
                "app.log", max_size, backup_count
            ),
            "error": self._create_handler(
                "error.log", max_size, backup_count, 
                min_level=logging.ERROR
            ),
            "metrics": self._create_handler(
                "metrics.log", max_size, backup_count
            ),
            "websocket": self._create_handler(
                "websocket.log", max_size, backup_count
            ),
            "audio": self._create_handler(
                "audio.log", max_size, backup_count
            )
        }
        
        # Setup root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(logging.INFO)
        
        # Add handlers to root logger
        for handler in self.handlers.values():
            root_logger.addHandler(handler)
    
    @classmethod
    def get_instance(cls, **kwargs) -> 'LogManager':
        """Get singleton instance"""
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = LogManager(**kwargs)
        return cls._instance
    
    def _create_handler(self,
                       filename: str,
                       max_size: int,
                       backup_count: int,
                       min_level: int = logging.INFO) -> logging.Handler:
        """Create a rotating file handler"""
        handler = RotatingFileHandler(
            self.log_dir / filename,
            maxBytes=max_size,
            backupCount=backup_count
        )
        handler.setLevel(min_level)
        handler.setFormatter(
            StructuredLogFormatter(
                app_name=self.app_name,
                log_type=filename.split('.')[0]
            )
        )
        return handler
    
    def get_logger(self, 
                  name: str,
                  extra: Optional[Dict[str, Any]] = None) -> logging.Logger:
        """Get a logger with the given name and extra fields"""
        logger = logging.getLogger(name)
        
        if extra:
            # Create a filter to add extra fields
            class ExtraFilter(logging.Filter):
                def filter(self, record):
                    record.extra = extra
                    return True
            
            logger.addFilter(ExtraFilter())
        
        return logger

class LogContext:
    """Context manager for temporarily adding fields to logs"""
    
    def __init__(self, logger: logging.Logger, **kwargs):
        self.logger = logger
        self.extra = kwargs
        self.old_extra = None
    
    def __enter__(self):
        # Store existing extra fields
        for filter in self.logger.filters:
            if hasattr(filter, 'extra'):
                self.old_extra = filter.extra
                filter.extra.update(self.extra)
                break
        else:
            # No existing filter, add new one
            class ExtraFilter(logging.Filter):
                def filter(self, record):
                    record.extra = self.extra
                    return True
            
            self.logger.addFilter(ExtraFilter())
        
        return self.logger
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.old_extra:
            # Restore old extra fields
            for filter in self.logger.filters:
                if hasattr(filter, 'extra'):
                    filter.extra = self.old_extra
                    break

def get_logger(name: str, 
               extra: Optional[Dict[str, Any]] = None) -> logging.Logger:
    """Convenience function to get a logger"""
    return LogManager.get_instance().get_logger(name, extra)

# Usage example:
# logger = get_logger(__name__, {"component": "websocket"})
# logger.info("Connection established", extra={"client_id": "123"})
#
# with LogContext(logger, request_id="abc123"):
#     logger.info("Processing request")  # Will include request_id
