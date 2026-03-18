"""
Gemini LLM service implementation
"""
from google import genai

from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_prompt_summary
from orchestrator_service.models.summary_models import SummaryActionItemsResult
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

    async def summarize_conversation(self, conversation_text: str) -> SummaryActionItemsResult:
        """
        Summarize conversation using Gemini API with structured JSON response.

        Args:
            conversation_text: Formatted conversation transcript

        Returns:
            SummaryActionItemsResult with summary and action items

        Raises:
            Exception: If Gemini API call fails
        """
        try:
            prompt = build_prompt_summary(conversation_text)

            response = self.client.models.generate_content(
                model=self.config.model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": SummaryActionItemsResult.model_json_schema(),
                },
            )

            summary_result = SummaryActionItemsResult.model_validate_json(response.text)
            logger.info("Successfully generated summary using Gemini")
            return summary_result

        except Exception as e:
            logger.error(f"Gemini summarization error: {e}")
            # Return error summary instead of raising
            return SummaryActionItemsResult(
                summary=f"An error occurred during summarization: {e}",
                action_items=[]
            )
