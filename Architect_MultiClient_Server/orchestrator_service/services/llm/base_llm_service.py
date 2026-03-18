"""
Base abstract class for all LLM service providers
"""
from abc import ABC, abstractmethod
from orchestrator_service.models.summary_models import SummaryActionItemsResult
from orchestrator_service.config.application_config import LLMConfig


class BaseLLMService(ABC):
    """Base abstract class for all LLM service providers"""

    def __init__(self, config: LLMConfig):
        """
        Initialize the LLM service with configuration.

        Args:
            config: LLMConfig containing provider settings
        """
        self.config = config

    @abstractmethod
    async def summarize_conversation(self, conversation_text: str) -> SummaryActionItemsResult:
        """
        Summarize conversation and extract action items.

        Args:
            conversation_text: Formatted conversation with timestamps and participants
                              Format: [time] participant_identity: transcript_text

        Returns:
            SummaryActionItemsResult with summary and action items

        Raises:
            Exception: If summarization fails
        """
        pass
