"""
Local LLM service (OpenAI-compatible API)
"""
import asyncio
import json
import logging
import re
from typing import Any, Dict, Optional
import httpx
from pydantic import ValidationError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
)

from orchestrator_service.config.application_config import LLMConfig, LLMProvider
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.gemini_llm_service import GeminiLLMService
from orchestrator_service.services.llm.prompt import build_simple_prompt_action_items, build_prompt_summary
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

        self._fallback_service: Optional[GeminiLLMService] = None
        if config.fallback_enabled and config.fallback_api_key:
            fallback_config = LLMConfig(
                provider=LLMProvider.GEMINI,
                api_key=config.fallback_api_key,
                model=config.fallback_model,
                language=config.language,
            )
            try:
                self._fallback_service = GeminiLLMService(fallback_config)
                logger.info(f"LLM fallback service initialized: model={config.fallback_model}")
            except Exception as e:
                logger.warning(f"Failed to initialize LLM fallback service: {e}")

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
        return extract_json_from_llm(result)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, ValidationError, RuntimeError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _summarize_summary_local(self, conversation_text: str, language: str) -> SummaryResult:
        logger.info(f"[Local LLM] Calling summarize_summary (model={self.config.model})")
        prompt = build_prompt_summary(conversation_text, language)
        json_data = await self._call_local_llm(prompt, SummaryResult.model_json_schema())
        result = SummaryResult.model_validate(json_data)
        logger.info("[Local LLM] summarize_summary succeeded")
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type((httpx.HTTPError, ValueError, ValidationError, RuntimeError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _summarize_action_items_local(self, conversation_text: str, language: str) -> ActionItemsResult:
        logger.info(f"[Local LLM] Calling summarize_action_items (model={self.config.model})")
        prompt = build_simple_prompt_action_items(conversation_text, language)
        json_data = await self._call_local_llm(prompt, ActionItemsResult.model_json_schema())
        result = ActionItemsResult.model_validate(json_data)
        logger.info("[Local LLM] summarize_action_items succeeded")
        return result

    async def summarize_summary(self, conversation_text: str, language: str) -> SummaryResult:
        try:
            return await self._summarize_summary_local(conversation_text, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.warning(f"[Local LLM] summarize_summary failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_summary(conversation_text, language)
                logger.info(f"[Fallback LLM] summarize_summary succeeded (model={self.config.fallback_model})")
                return result
            raise

    async def summarize_action_items(self, conversation_text: str, language: str) -> ActionItemsResult:
        try:
            return await self._summarize_action_items_local(conversation_text, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.warning(f"[Local LLM] summarize_action_items failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_action_items(conversation_text, language)
                logger.info(f"[Fallback LLM] summarize_action_items succeeded (model={self.config.fallback_model})")
                return result
            raise

    async def summarize_conversation(self, conversation_text: str, room_id: str, language: str = "Vietnamese") -> SummaryActionItemsResult:
        """
        Summarize conversation by running 2 focused LLM requests concurrently.
        Each sub-task retries 3 times on local LLM then falls back to Gemini once.
        Returns success=False for any part that exhausts all attempts.

        Args:
            conversation_text: Formatted conversation transcript
            room_id: Room identifier for logging
            language: Output language

        Returns:
            SummaryActionItemsResult with summary and action items
        """
        results = await asyncio.gather(
            self.summarize_summary(conversation_text, language),
            self.summarize_action_items(conversation_text, language),
            return_exceptions=True,
        )
        summary_res, action_items_res = results

        summary_failed = isinstance(summary_res, Exception)
        action_items_failed = isinstance(action_items_res, Exception)

        if summary_failed:
            logger.error(f"Failed to generate summary (all attempts exhausted) with room_id: {room_id}: {summary_res}")
            summary = ""
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

        if action_items_failed:
            logger.error(f"Failed to generate action items (all attempts exhausted) with room_id: {room_id}: {action_items_res}")
            action_items = []
        else:
            action_items = action_items_res.action_items

        if not summary_failed and not action_items_failed:
            logger.info(f"Successfully generated summary and action items with room_id: {room_id}")

        return SummaryActionItemsResult(
            summary=summary,
            action_items=action_items,
            summary_success=not summary_failed,
            action_items_success=not action_items_failed,
        )
