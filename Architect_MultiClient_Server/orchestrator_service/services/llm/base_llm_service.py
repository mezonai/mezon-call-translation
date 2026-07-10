"""
Base abstract class for all LLM service providers
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

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
    async def generate(
        self, prompt: str, response_model: type[BaseModel], model: str, temperature: float, timeout: int
    ) -> BaseModel:
        """fuction doc"""
        pass
