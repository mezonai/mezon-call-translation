import asyncio
import os
from typing import Any, TypeVar

from google import genai
from google.genai import errors as genai_errors
from pydantic import BaseModel

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.utils.llm_utils import extract_json_from_llm
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.rate_limiter import estimate_tokens, get_rate_limiter

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

GEMINI_TOKENS_PER_MINUTE = int(os.getenv("GEMINI_TOKENS_PER_MINUTE", "16000"))


def _extract_retry_delay_seconds(error_payload: Any) -> float | None:  # type: ignore[explicit-any] # noqa: ANN401
    """Parse Gemini's 429 RetryInfo.retryDelay (e.g. "39s") out of the raw error payload, if present."""
    try:
        details = error_payload.get("error", {}).get("details", [])
    except AttributeError:
        return None

    for item in details:
        if isinstance(item, dict) and str(item.get("@type", "")).endswith("RetryInfo"):
            delay = item.get("retryDelay")
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    return None
    return None

class GeminiLLMService(BaseLLMService):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self.client = genai.Client(api_key=config.api_key)

    async def generate(
        self, prompt: str, response_model: type[T], model: str, temperature: float, top_p: float, timeout: int
    ) -> T:
        limiter = get_rate_limiter(model, GEMINI_TOKENS_PER_MINUTE)
        await limiter.acquire(estimate_tokens(prompt))

        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config={
                        "response_mime_type": "application/json",
                        "response_json_schema": response_model.model_json_schema(),
                        "temperature": temperature,
                        "top_p": top_p
                    },
                ),
                timeout=timeout
            )
        except genai_errors.APIError as e:
            retry_delay = _extract_retry_delay_seconds(e.details)
            if retry_delay is not None:
                e.retry_delay_seconds = retry_delay  # type: ignore[attr-defined]
                logger.warning(f"Gemini rate-limited (model={model}): server retryDelay={retry_delay}s")
            raise

        if response.text is None:
            raise ValueError("Empty response text from Gemini API")
        parsed_json = extract_json_from_llm(response.text)
        return response_model.model_validate(parsed_json)
