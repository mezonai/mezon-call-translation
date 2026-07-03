"""
Local LLM service (OpenAI-compatible API)
"""

import asyncio
import logging
from typing import TypeVar, cast

from openai import APIConnectionError, APIError, AsyncOpenAI, LengthFinishReasonError, RateLimitError
from pydantic import BaseModel, ValidationError
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from orchestrator_service.config.application_config import LLMConfig, LLMProvider
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryActionItemsResult, SummaryResult
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.gemini_llm_service import GeminiLLMService
from orchestrator_service.services.llm.prompt import (
    build_simple_prompt_action_items,
    build_prompt_summary,
    build_light_summary_prompt,
    build_overall_context_prompt
)
from orchestrator_service.models.summary_models import (
    ActionItemsResult,
    SummaryActionItemsResult,
    SummaryResult,
    LightSummaryResult,
    OverallContextResult
)
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


class LocalLLMService(BaseLLMService):
    """Local LLM service for OpenAI-compatible API"""

    def __init__(self, config: LLMConfig):
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

        base_url = config.base_url.replace("/chat/completions", "").rstrip("/")

        self.client = AsyncOpenAI(
            base_url=base_url,
            api_key=config.api_key,
            timeout=config.timeout,
        )

        logger.info(f"Initialized Local LLM service: {config.base_url}, model: {config.model}")

        self._fallback_service: GeminiLLMService | None = None
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

    async def _call_local_llm(self, prompt: str, response_model: type[TModel]) -> TModel:
        response = await self.client.beta.chat.completions.parse(
            model=self.config.model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_model,
            temperature=0.4,
            max_tokens=15000,
        )
        result = response.choices[0].message.parsed
        if result is None:
            raise ValueError("LLM returned no structured output")
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type(
            (
                APIError,
                APIConnectionError,
                RateLimitError,
                LengthFinishReasonError,
                ValueError,
                ValidationError,
                RuntimeError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
    )
    async def _summarize_summary_local(self, conversation_text: str, language: str) -> SummaryResult:
        logger.info(f"[Local LLM] Calling summarize_summary (model={self.config.model})")
        prompt = build_prompt_summary(conversation_text, language)
        result = await self._call_local_llm(prompt, SummaryResult)
        logger.info("[Local LLM] summarize_summary succeeded")
        return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type(
            (
                APIError,
                APIConnectionError,
                RateLimitError,
                LengthFinishReasonError,
                ValueError,
                ValidationError,
                RuntimeError,
            )
        ),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
    )
    async def _summarize_action_items_local(self, conversation_text: str, language: str) -> ActionItemsResult:
        logger.info(f"[Local LLM] Calling summarize_action_items (model={self.config.model})")
        prompt = build_simple_prompt_action_items(conversation_text, language)
        result = await self._call_local_llm(prompt, ActionItemsResult)
        logger.info("[Local LLM] summarize_action_items succeeded")
        return result

    async def summarize_summary(self, conversation_text: str, language: str) -> SummaryResult:
        try:
            return await self._summarize_summary_local(conversation_text, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.error(f"[Local LLM] summarize_summary failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_summary(conversation_text, language)
                logger.info(f"[Fallback LLM] summarize_summary succeeded (model={self.config.fallback_model})")
                return result
            raise

    async def summarize_action_items(self, conversation_text: str, language: str) -> ActionItemsResult:
        try:
            return await self._summarize_action_items_local(conversation_text, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.error(f"[Local LLM] summarize_action_items failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_action_items(conversation_text, language)
                logger.info(f"[Fallback LLM] summarize_action_items succeeded (model={self.config.fallback_model})")
                return result
            raise

    async def summarize_conversation(
        self, conversation_text: str, room_id: str, language: str = "Vietnamese"
    ) -> SummaryActionItemsResult:
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
            summary_parts = [f"Context\n{summary_res.context}"]
            if summary_res.key_discussions:
                summary_parts.append("Key Discussions\n" + "\n".join(summary_res.key_discussions))

            if summary_res.next_focus:
                summary_parts.append("Next Focus\n" + "\n".join(summary_res.next_focus))

            if summary_res.detail:
                summary_parts.append("Detail\n" + "\n".join(summary_res.detail))
            
            summary = "\n\n".join(summary_parts)

        if action_items_failed:
            logger.error(
                f"Failed to generate action items (all attempts exhausted) with room_id: {room_id}: {action_items_res}"
            )
            action_items = []
        else:
            valid_action_items = cast(ActionItemsResult, action_items_res)
            action_items = valid_action_items.action_items

        if not summary_failed and not action_items_failed:
            logger.info(f"Successfully generated summary and action items with room_id: {room_id}")

        return SummaryActionItemsResult(
            summary=summary,
            action_items=action_items,
            summary_success=not summary_failed,
            action_items_success=not action_items_failed,
        )
    
    @retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=10, max=60),
    retry=retry_if_exception_type((APIError, APIConnectionError, RateLimitError, LengthFinishReasonError, ValueError, ValidationError, RuntimeError)),
    before_sleep=before_sleep_log(logger, logging.ERROR),
    reraise=True,
    )
    async def _summarize_light_section_local(self, conversation_str: str, previous_context: str, language: str) -> LightSummaryResult:
        prompt = build_light_summary_prompt(conversation_str, previous_context, language)
        return await self._call_local_llm(prompt, LightSummaryResult)

    async def summarize_light_section(self, conversation_str: str, previous_context: str, language: str) -> LightSummaryResult:
        try:
            return await self._summarize_light_section_local(conversation_str, previous_context, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.error(f"[Local LLM] summarize_light_section failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_light_section(conversation_str, previous_context, language)
                logger.info(f"[Fallback LLM] summarize_light_section succeeded (model={self.config.fallback_model})")
                return result
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=10, max=60),
        retry=retry_if_exception_type((APIError, APIConnectionError, RateLimitError, LengthFinishReasonError, ValueError, ValidationError, RuntimeError)),
        before_sleep=before_sleep_log(logger, logging.ERROR),
        reraise=True,
    )
    async def _summarize_overall_context_local(self, section_context_str: str, language: str) -> OverallContextResult:
        prompt = build_overall_context_prompt(section_context_str, language)
        return await self._call_local_llm(prompt, OverallContextResult)

    async def summarize_overall_context(self, section_context_str: str, language: str) -> OverallContextResult:
        try:
            return await self._summarize_overall_context_local(section_context_str, language)
        except Exception as e:
            if self._fallback_service is not None:
                logger.error(f"[Local LLM] summarize_overall_context failed after all retries, switching to fallback: {e}")
                result = await self._fallback_service.summarize_overall_context(section_context_str, language)
                logger.info(f"[Fallback LLM] summarize_overall_context succeeded (model={self.config.fallback_model})")
                return result
            raise

