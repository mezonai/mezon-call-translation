class BusinessException(Exception):  # noqa: N818
    """Base exception for all business and domain errors."""

    error_code: str = "BUSINESS_ERROR"


# Alias for application error, do not change logic in api layer
ApplicationError = BusinessException


# Resources not found
class ResourceNotFoundError(BusinessException):
    """Raised when a requested resource does not exist."""

    error_code: str = "RESOURCE_NOT_FOUND"


class RoomNotFoundError(ResourceNotFoundError):
    """Raised when a requested room does not exist."""

    error_code: str = "ROOM_NOT_FOUND"


class RoomTracksNotFoundError(ResourceNotFoundError):
    """Raised when requested room tracks do not exist."""

    error_code: str = "ROOM_TRACKS_NOT_FOUND"


class MetadataEventNotFoundError(ResourceNotFoundError):
    """Raised when a requested metadata event does not exist."""

    error_code: str = "METADATA_EVENT_NOT_FOUND"


class UserNotFoundError(ResourceNotFoundError):
    """Raised when a requested user does not exist."""

    error_code: str = "USER_NOT_FOUND"


class QueueNotFoundError(ResourceNotFoundError):
    """Raised when a requested queue does not exist."""

    error_code: str = "QUEUE_NOT_FOUND"


class SummaryRetryNotFoundError(ResourceNotFoundError):
    """Raised when a requested summary retry does not exist."""

    error_code: str = "SUMMARY_RETRY_NOT_FOUND"


# Resources are denied to access
class AccessDeniedError(BusinessException):
    """Raised when a requested resource is denied to access."""

    error_code: str = "ACCESS_DENIED"


class RoomAccessDeniedError(AccessDeniedError):
    """Raised when a requested room access is denied."""

    error_code: str = "ROOM_ACCESS_DENIED"


# Authentication failed
class AuthenticationFailedError(BusinessException):
    """Raised when authentication fails."""

    error_code: str = "AUTHENTICATION_FAILED"


class InvalidRefreshTokenError(AuthenticationFailedError):
    """Raised when a refresh token is invalid."""

    error_code: str = "INVALID_REFRESH_TOKEN"


class AccountAuthenticationFailedError(AuthenticationFailedError):
    """Raised when account authentication fails."""

    error_code: str = "ACCOUNT_AUTHENTICATION_FAILED"


# Invalid state
class InvalidStateError(BusinessException):
    """Raised when a requested resource is in an invalid state for the operation."""

    error_code: str = "INVALID_STATE"


# Integrations errors
class IntegrationError(BusinessException):
    """Raised when an integration call fails."""

    error_code: str = "INTEGRATION_ERROR"


class MezonIntegrationError(IntegrationError):
    """Raised when a Mezon integration call fails."""

    error_code: str = "MEZON_INTEGRATION_ERROR"


# Internal service errors
class InternalServiceError(BusinessException):
    """Raised when an internal service error occurs."""

    error_code: str = "INTERNAL_SERVICE_ERROR"


class TokenGenerationError(InternalServiceError):
    """Raised when token generation fails."""

    error_code: str = "TOKEN_GENERATION_ERROR"


class RefreshTokenRotationError(InternalServiceError):
    """Raised when a refresh token rotation fails."""

    error_code: str = "REFRESH_TOKEN_ROTATION_ERROR"


class AuthConfigurationError(InternalServiceError):
    """Raised when auth configuration is invalid."""

    error_code: str = "AUTH_CONFIGURATION_ERROR"


class UnsupportedLlmProviderError(InternalServiceError):
    """Raised when an unsupported LLM provider is encountered."""

    error_code: str = "UNSUPPORTED_LLM_PROVIDER_ERROR"


class SummaryGenerationError(IntegrationError):
    """Raised when summary generation fails."""

    error_code: str = "SUMMARY_GENERATION_ERROR"


class LlmInvalidResponseError(IntegrationError):
    """Raised when an LLM returns an invalid response."""

    error_code: str = "LLM_INVALID_RESPONSE_ERROR"


class RoomSummaryNotFoundError(ResourceNotFoundError):
    """Raised when a requested room summary does not exist."""

    error_code: str = "ROOM_SUMMARY_NOT_FOUND"


class SummarySectionsUnavailableError(InvalidStateError):
    """Raised when summary sections are unavailable for creating a full summary."""

    error_code: str = "SUMMARY_SECTIONS_UNAVAILABLE"


class SummaryPersistenceError(InternalServiceError):
    """Raised when summary persistence fails."""

    error_code: str = "SUMMARY_PERSISTENCE_ERROR"


# Redis connection error
class InfrastructureError(BusinessException):
    """Raised when an infrastructure error occurs."""

    error_code: str = "INFRASTRUCTURE_ERROR"


class RedisConnectionError(InfrastructureError):
    """Raised when a Redis connection error occurs."""

    error_code: str = "REDIS_CONNECTION_ERROR"


class RedisPoolNotInitializedError(InternalServiceError):
    """Raised when a Redis connection pool is not initialized."""

    error_code: str = "REDIS_POOL_NOT_INITIALIZED_ERROR"


class TrackCompletionError(InternalServiceError):
    """Raised when a track completion error occurs."""

    error_code: str = "TRACK_COMPLETION_ERROR"
