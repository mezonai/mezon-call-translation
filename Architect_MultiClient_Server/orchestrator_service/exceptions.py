class ApplicationError(Exception):
    """Base exception for application-specific errors."""
class QueueNotFoundError(ApplicationError):
    """Raised when a requested queue does not exist."""
class SummaryRetryNotFoundError(ApplicationError):
    """Raised when a requested summary for retry does not exist."""
