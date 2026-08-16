from datetime import datetime

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def parse_timestamp_to_seconds(ts: str) -> float:
    """
    Parse a timestamp string in HH:MM:SS format into total seconds.

    Args:
        ts: The timestamp string to parse (e.g., "01:23:45")

    Returns:
        The total number of seconds as a float. Returns 0.0 if the format is invalid.
    """
    ts_str = str(ts).strip()

    try:
        t = datetime.strptime(ts_str, "%H:%M:%S")
        return t.hour * 3600 + t.minute * 60 + t.second
    except ValueError:
        logger.warning(f"Invalid summary timestamp: {ts_str}")
        return 0.0
