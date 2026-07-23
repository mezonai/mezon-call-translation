"""
Base abstract class for all LLM service providers
"""

from abc import ABC, abstractmethod
from typing import TypeVar

from pydantic import BaseModel

from orchestrator_service.config.application_config import LLMConfig

T = TypeVar("T", bound=BaseModel)
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
        self, prompt: str, response_model: type[T], model: str, temperature: float, top_p: float, timeout: int
    ) -> T:
        """fuction doc"""
        pass
