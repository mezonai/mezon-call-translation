import logging

# Configure logging with custom format
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)

# Set specific log levels for different loggers
logging.getLogger('websockets').setLevel(logging.WARNING)  # Reduce websocket noise
logging.getLogger('asyncio').setLevel(logging.WARNING)    # Reduce asyncio noise

# Create metrics logger
metrics_logger = logging.getLogger('metrics')
metrics_logger.setLevel(logging.INFO)

logger = logging.getLogger(__name__)

def get_logger(name: str) -> logging.Logger:
    """Get logger with consistent formatting"""
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)  # Ensure new loggers also have INFO level
    return logger