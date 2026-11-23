class ApplicationError(Exception):
    """Base exception for application-specific errors."""
class QueueNotFoundError(ApplicationError):
    """Raised when a requested queue does not exist."""
class SummaryRetryNotFoundError(ApplicationError):
    """Raised when a requested summary for retry does not exist."""


class TopicCompletionNotFoundError(ValueError, ApplicationError):
    """Raised when LLM cannot find a completed topic even after extending windows to max_duration."""
