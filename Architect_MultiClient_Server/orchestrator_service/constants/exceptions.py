from openai import APIError, APIConnectionError, RateLimitError, LengthFinishReasonError
from google.genai import errors as genai_errors
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
)