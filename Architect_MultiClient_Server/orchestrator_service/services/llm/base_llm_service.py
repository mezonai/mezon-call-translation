"""
Base abstract class for all LLM service providers
"""

from abc import ABC, abstractmethod

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryResult


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
    async def summarize_summary(self, conversation_text: str, language: str) -> SummaryResult:
        """
        Generate only summary/context from conversation transcript.

        Args:
            conversation_text: Formatted conversation with timestamps and participants
                              Format: [time] participant_identity: transcript_text

        Returns:
            SummaryResult containing only summary

        Raises:
            Exception: If generation fails
        """
        pass

    @abstractmethod
    async def summarize_action_items(self, conversation_text: str, language: str) -> ActionItemsResult:
        """
        Extract only action items from conversation transcript.

        Args:
            conversation_text: Formatted conversation with timestamps and participants
                              Format: [time] participant_identity: transcript_text

        Returns:
            ActionItemsResult containing only action items

        Raises:
            Exception: If extraction fails
        """
        pass