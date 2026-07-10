"""
Factory for creating LLM service instances based on provider type
"""

from orchestrator_service.config.application_config import LLMProvider, get_config
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.gemini_llm_service import GeminiLLMService
from orchestrator_service.services.llm.local_llm_service import LocalLLMService
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def create_llm_service(provider: str, model: str, temperature: float) -> BaseLLMService:
    """
    Factory function to create LLM service based on provider type.

    Supported providers:
    - 'gemini': Google Gemini API (also handles Gemma models)
    - 'local': Local OpenAI-compatible LLM
    Args:
        config: LLMConfig with provider type and credentials

    Returns:
        Concrete implementation of BaseLLMService

    Raises:
        ValueError: If provider is unknown or unsupported
    """
    provider = provider.lower()
    config = get_config()

    if provider == LLMProvider.GEMINI:
        llm_config = config.gemini_llm_config
        service = GeminiLLMService(llm_config)
    elif provider == LLMProvider.LOCAL:
        llm_config = config.local_llm_config
        service = LocalLLMService(llm_config)
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported providers: {LLMProvider.GEMINI.value}, {LLMProvider.LOCAL.value}"
        )

    logger.info(f"Creating LLM service for provider: {provider}, model: {model}, temperature: {temperature}")
    return service
