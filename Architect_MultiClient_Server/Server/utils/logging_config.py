import logging
import sys


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

    console_handler = logging.StreamHandler(stream=sys.stdout)
    console_handler.setLevel(log_level)
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


