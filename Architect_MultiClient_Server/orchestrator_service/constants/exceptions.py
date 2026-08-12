from google.genai import errors as genai_errors
from openai import APIConnectionError, APIError, LengthFinishReasonError, RateLimitError
from pydantic import ValidationError

RETRYABLE_EXCEPTIONS = (
    APIError,
    APIConnectionError,
    RateLimitError,
    LengthFinishReasonError,
    genai_errors.APIError,
    ValueError,
    ValidationError,
    RuntimeError,
    TimeoutError,
)
