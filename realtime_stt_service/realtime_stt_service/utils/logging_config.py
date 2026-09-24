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
    app_handler = logging.FileHandler('logs/app.log', encoding='utf-8')
    app_handler.setLevel(log_level)
    app_handler.setFormatter(formatter)
    
    # Chỉ lấy các log không phải metrics
    class NoMetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" not in record.getMessage()
    
    app_handler.addFilter(NoMetricsFilter())
    root_logger.addHandler(app_handler)

    # File handler cho metrics
    metrics_handler = logging.FileHandler('logs/metrics.log', encoding='utf-8')
    metrics_handler.setLevel(log_level)
    metrics_handler.setFormatter(formatter)
    
    # Chỉ lấy các log metrics
    class MetricsFilter(logging.Filter):
        def filter(self, record):
            return "Metrics |" in record.getMessage()
    
    metrics_handler.addFilter(MetricsFilter())
    root_logger.addHandler(metrics_handler)


