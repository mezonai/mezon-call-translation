"""
Local LLM service (OpenAI-compatible API)
"""
import json
import logging
import re
from typing import Any, Dict
from orchestrator_service.utils.logger import get_logger
import httpx
from pydantic import ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_prompt_summary
from orchestrator_service.models.summary_models import SummaryActionItemsResult
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def extract_json_from_llm(result: dict) -> Dict[str, Any]:
    """
    Safely extract JSON payload from OpenAI-compatible chat completion responses.

    Args:
        result: Raw API response dictionary

    Returns:
        Extracted JSON dictionary

    Raises:
        RuntimeError: If response format is invalid
        ValueError: If no valid JSON found in response
    """
    if "choices" not in result:
        logger.error("Invalid local LLM API response: missing 'choices'")
        logger.debug(json.dumps(result, indent=2, ensure_ascii=False))
        raise RuntimeError("Invalid API response")

    msg = result["choices"][0].get("message", {})
    raw = (msg.get("content") or msg.get("reasoning_content") or "").strip()

    if not raw:
        raise ValueError("Empty LLM output")

    # 1) Direct JSON parse
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Direct JSON parse failed, attempting to extract JSON from LLM output")
        pass

    # 2) Extract balanced JSON object candidates
    stack = []
    start = None
    candidates = []
    for i, ch in enumerate(raw):
        if ch == "{":
            if not stack:
                start = i
            stack.append(ch)
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    candidates.append(raw[start : i + 1])

    for candidate in reversed(candidates):
        try:
            return json.loads(candidate)
        except Exception:
            logger.warning("Candidate JSON parse failed, trying next candidate")
            continue

    # 3) Extract JSON in markdown code blocks
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except Exception:
            logger.warning("Markdown code block JSON parse failed, trying next block")
            continue

    logger.error("Cannot extract JSON from local LLM output")
    raise ValueError("No valid JSON found in LLM response")


class LocalLLMService(BaseLLMService):
    """Local LLM service for OpenAI-compatible API"""

    def __init__(self, config):
        """
        Initialize local LLM service.

        Args:
            config: LLMConfig with base_url, model, api_key, timeout

        Raises:
            ValueError: If base_url is not provided
        """
        super().__init__(config)
        if not config.base_url:
            raise ValueError("base_url is required for LocalLLMService")
        logger.info(f"Initialized Local LLM service: {config.base_url}, model: {config.model}")

    def _build_headers(self) -> Dict[str, str]:
        """
        Build HTTP headers for API requests.

        Returns:
            Dictionary of HTTP headers
        """
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        return headers

    @retry(
        stop=stop_after_attempt(3),  # Maximum 3 retry attempts
        wait=wait_exponential(multiplier=1, min=10, max=60),  # 10s → 20s → 40s
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, ValidationError)),  # Retry on HTTP/parse/validation errors
        before_sleep=before_sleep_log(logger, logging.WARNING),  # Log before each retry
        reraise=True,  # Re-raise the exception after all retries exhausted
    )
    async def summarize_conversation(self, conversation_text: str) -> SummaryActionItemsResult:
        """
        Summarize conversation using local OpenAI-compatible LLM with automatic retry.

        This method automatically retries on failures with exponential backoff:
        - Max retries: 3 attempts
        - Backoff: 10s → 20s → 40s (max 60s)
        - Retries on: HTTP errors, connection issues, JSON parse failures, and validation errors

        Args:
            conversation_text: Formatted conversation transcript

        Returns:
            SummaryActionItemsResult with summary and action items

        Raises:
            httpx.HTTPStatusError: If API request fails after all retries
            ValueError: If response cannot be parsed after all retries
            ValidationError: If parsed JSON doesn't match schema after all retries
        """
        prompt = build_prompt_summary(conversation_text)

        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "json_schema": SummaryActionItemsResult.model_json_schema(),
            "max_tokens": 15000,
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=self.config.timeout) as client:
            response = await client.post(
                self.config.base_url,
                headers=self._build_headers(),
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        json_data = extract_json_from_llm(result)
        # Validate and parse JSON into SummaryActionItemsResult
        summary_result = SummaryActionItemsResult.model_validate(json_data)
        logger.info("Successfully generated summary using Local LLM")
        return summary_result
