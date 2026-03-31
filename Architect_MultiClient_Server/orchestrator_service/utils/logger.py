import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from orchestrator_service.config.application_config import get_config

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # project root
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)


log_level_str = get_config().logger.level
log_level = getattr(logging, log_level_str, logging.INFO)

def setup_logger(name: str) -> logging.Logger:
    """Setup logging configuration for a new logger"""
    logger = logging.getLogger(name)

    # Only configure logger if it hasn't been configured yet
    if not logger.handlers:
        # Create formatters
        console_formatter = logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        
        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(console_formatter)
        console_handler.setLevel(log_level)
        logger.propagate = False
        logger.addHandler(console_handler)
        
        # Optional file handler for specific loggers
        if name == 'metrics':
            file_handler = RotatingFileHandler(
                os.path.join(LOG_DIR, "metrics.log"),
                maxBytes=10*1024*1024,  # 10MB
                backupCount=5
            )
            file_formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
            file_handler.setFormatter(file_formatter)
            file_handler.setLevel(log_level)
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