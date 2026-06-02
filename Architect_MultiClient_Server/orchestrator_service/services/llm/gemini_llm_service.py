"""
Gemini LLM service implementation
"""
from google import genai

from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_prompt_action_items, build_prompt_summary
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryActionItemsResult, SummaryResult
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class GeminiLLMService(BaseLLMService):
    """Gemini LLM service implementation using Google Generative AI SDK"""

    def __init__(self, config):
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
        return SummaryResult.model_validate_json(response.text)

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
        return ActionItemsResult.model_validate_json(response.text)

    async def summarize_conversation(self, conversation_text: str, language: str = "Vietnamese") -> SummaryActionItemsResult:
        """Run 2 Gemini requests: one for summary and one for action items."""
        try:
            summary_result = await self.summarize_summary(conversation_text, language)
            action_items_result = await self.summarize_action_items(conversation_text, language)
            logger.info("Successfully generated summary and action items using Gemini (2 requests)")

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
                is_success=True,
            )
        except Exception as e:
            logger.error(f"Gemini summarization error: {e}")
            return SummaryActionItemsResult(
                summary=f"An error occurred during summarization: {e}",
                action_items=[],
                summary_success=False,
                action_items_success=False,
                is_success=False,
            )
