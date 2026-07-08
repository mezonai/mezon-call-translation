"""
Gemini LLM service implementation
"""

import json
import re
from typing import Any

from google import genai

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryActionItemsResult, SummaryResult
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_prompt_action_items, build_prompt_summary
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


# TODO: Use `Any` type because `json.loads()` parses dynamic structures from LLM responses that are not pre-defined.
def extract_json_from_llm(raw_text: str) -> dict[str, Any]:  # type: ignore[explicit-any]
    """
    Safely extract JSON payload from OpenAI-compatible chat completion responses.

    Args:
        raw_text: Raw API response string

    Returns:
        Extracted JSON dictionary

    Raises:
        RuntimeError: If response format is invalid
        ValueError: If no valid JSON found in response
    """
    raw = raw_text.strip()
    if not raw:
        raise ValueError("Empty LLM output")

    # 1) Direct JSON parse
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        logger.warning(f"Direct JSON parse failed, attempting to extract JSON from LLM outputL: {raw}")
        pass

    # 2) Extract balanced JSON object candidates
    stack: list[str] = []
    start = None
    candidates: list[str] = []
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
            return json.loads(candidate)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Candidate JSON parse failed, trying next candidate")
            continue

    # 3) Extract JSON in markdown code blocks
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Markdown code block JSON parse failed, trying next block")
            continue

    logger.error("Cannot extract JSON from local LLM output")
    raise ValueError("No valid JSON found in LLM response")


class GeminiLLMService(BaseLLMService):
    """Gemini LLM service implementation using Google Generative AI SDK"""

    def __init__(self, config: LLMConfig):
        """
        Initialize Gemini service with API client.

        Args:
            config: LLMConfig with Gemini API key and model name

        Raises:
            Exception: If Gemini client initialization fails
        """
        super().__init__(config)
        try:
            self.client = genai.Client(api_key=config.api_key)
            logger.info(f"Initialized Gemini LLM service with model: {config.model}")
        except Exception as e:
            logger.error(f"Failed to initialize Gemini client: {e}")
            raise

    async def summarize_summary(self, conversation_text: str, language: str) -> SummaryResult:
        prompt = build_prompt_summary(conversation_text, language)
        response = await self.client.aio.models.generate_content(
            model=self.config.model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": SummaryResult.model_json_schema(),
            },
        )

        raw_text = response.text
        if not raw_text:
            raise ValueError("Empty response text from Gemini API")

        return SummaryResult.model_validate(extract_json_from_llm(raw_text))

    async def summarize_action_items(self, conversation_text: str, language: str) -> ActionItemsResult:
        prompt = build_prompt_action_items(conversation_text, language)
        response = await self.client.aio.models.generate_content(
            model=self.config.model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": ActionItemsResult.model_json_schema(),
            },
        )

        raw_text = response.text
        if not raw_text:
            raise ValueError("Empty response text from Gemini API")

        return ActionItemsResult.model_validate(extract_json_from_llm(raw_text))

    async def summarize_conversation(
        self, conversation_text: str, room_id: str, language: str = "Vietnamese"
    ) -> SummaryActionItemsResult:
        """Run 2 Gemini requests: one for summary and one for action items."""
        try:
            summary_result = await self.summarize_summary(conversation_text, language)
            action_items_result = await self.summarize_action_items(conversation_text, language)
            logger.info(
                f"Successfully generated summary and action items using Gemini (2 requests) for room: {room_id}"
            )

            # Build summary with only non-empty fields
            summary_parts = [
                f"Context\n{summary_result.context}",
                f"Key Discussions\n{summary_result.key_discussions}",
            ]

            if summary_result.decisions and summary_result.decisions.strip():
                summary_parts.append(f"Decisions\n{summary_result.decisions}")

            if summary_result.unresolved_issues and summary_result.unresolved_issues.strip():
                summary_parts.append(f"Unresolved Issues\n{summary_result.unresolved_issues}")

            if summary_result.next_focus and summary_result.next_focus.strip():
                summary_parts.append(f"Next Focus\n{summary_result.next_focus}")

            return SummaryActionItemsResult(
                summary="\n\n".join(summary_parts),
                action_items=action_items_result.action_items,
                summary_success=True,
                action_items_success=True,
            )
        except Exception as e:
            logger.error(f"Gemini summarization error for room {room_id}: {e}")
            return SummaryActionItemsResult(
                summary=f"An error occurred during summarization: {e}",
                action_items=[],
                summary_success=False,
                action_items_success=False,
            )
