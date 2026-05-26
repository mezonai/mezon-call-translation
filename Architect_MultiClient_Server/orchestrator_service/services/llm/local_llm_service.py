"""
Local LLM service (OpenAI-compatible API)
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict
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
from orchestrator_service.services.llm.prompt import build_prompt_action_items, build_prompt_summary
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryActionItemsResult, SummaryResult
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
        logger.warning(f"Direct JSON parse failed, attempting to extract JSON from LLM outputL: {raw}")
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

    async def _call_local_llm(self, prompt: str, json_schema: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "json_schema": json_schema,
            "max_tokens": 150000,
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
        return extract_json_from_llm(result)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, ValidationError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def summarize_summary(self, conversation_text: str, language: str) -> SummaryResult:
        prompt = build_prompt_summary(conversation_text, language)
        json_data = await self._call_local_llm(prompt, SummaryResult.model_json_schema())
        return SummaryResult.model_validate(json_data)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, ValidationError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def summarize_action_items(self, conversation_text: str, language: str) -> ActionItemsResult:
        prompt = build_prompt_action_items(conversation_text, language)
        json_data = await self._call_local_llm(prompt, ActionItemsResult.model_json_schema())
        return ActionItemsResult.model_validate(json_data)

    async def summarize_conversation(self, conversation_text: str, language: str = "Vietnamese") -> SummaryActionItemsResult:
        """
        Summarize conversation by running 2 focused local LLM requests.

        Args:
            conversation_text: Formatted conversation transcript

        Returns:
            SummaryActionItemsResult with summary and action items
        """
        summary_task = self.summarize_summary(conversation_text, language)
        action_items_task = self.summarize_action_items(conversation_text, language)

        results = await asyncio.gather(summary_task, action_items_task, return_exceptions=True)
        summary_res, action_items_res = results

        # Process summary result
        if isinstance(summary_res, Exception):
            logger.error(f"Failed to generate summary using Local LLM: {summary_res}")
            summary = f"An error occurred during summary generation: {summary_res}"
        else:
            summary_parts = [
                f"Context\n{summary_res.context}",
                f"Key Discussions\n{summary_res.key_discussions}",
            ]

            if summary_res.decisions and summary_res.decisions.strip():
                summary_parts.append(f"Decisions\n{summary_res.decisions}")

            if summary_res.unresolved_issues and summary_res.unresolved_issues.strip():
                summary_parts.append(f"Unresolved Issues\n{summary_res.unresolved_issues}")

            if summary_res.next_focus and summary_res.next_focus.strip():
                summary_parts.append(f"Next Focus\n{summary_res.next_focus}")

            summary = "\n\n".join(summary_parts)

        # Process action items result
        if isinstance(action_items_res, Exception):
            logger.error(f"Failed to generate action items using Local LLM: {action_items_res}")
            action_items = []
        else:
            action_items = action_items_res.action_items

        if not isinstance(summary_res, Exception) and not isinstance(action_items_res, Exception):
            logger.info("Successfully generated summary and action items using Local LLM (2 requests)")

        return SummaryActionItemsResult(
            summary=summary,
            action_items=action_items,
        )
