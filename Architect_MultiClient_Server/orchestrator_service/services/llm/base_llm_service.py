"""
Base abstract class for all LLM service providers
"""
from abc import ABC, abstractmethod
from orchestrator_service.models.summary_models import ActionItemsResult, SummaryActionItemsResult, SummaryResult
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
    async def summarize_summary(self, conversation_text: str) -> SummaryResult:
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
    async def summarize_action_items(self, conversation_text: str) -> ActionItemsResult:
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

    async def summarize_conversation(self, conversation_text: str) -> SummaryActionItemsResult:
        """Backward-compatible helper that combines 2 focused LLM requests."""
        summary_result = await self.summarize_summary(conversation_text)
        action_items_result = await self.summarize_action_items(conversation_text)
        return SummaryActionItemsResult(
            summary=(
                f"CONTEXT\n{summary_result.context}\n\n"
                f"KEY DISCUSSIONS\n{summary_result.key_discussions}\n\n"
                f"DECISIONS\n{summary_result.decisions}\n\n"
                f"UNRESOLVED ISSUES\n{summary_result.unresolved_issues}\n\n"
                f"NEXT FOCUS\n{summary_result.next_focus}"
            ),
            action_items=action_items_result.action_items,
        )
