"""
Factory for creating LLM service instances based on provider type
"""
from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.gemini_llm_service import GeminiLLMService
from orchestrator_service.services.llm.local_llm_service import LocalLLMService
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def create_llm_service(config: LLMConfig) -> BaseLLMService:
    """
    Factory function to create LLM service based on provider type.

    Supported providers:
    - 'gemini': Google Gemini API
    - 'gemma': Google Gemma models (via Generative AI SDK)
    - 'local': Local OpenAI-compatible LLM
    Args:
        config: LLMConfig with provider type and credentials

    Returns:
        Concrete implementation of BaseLLMService

    Raises:
        ValueError: If provider is unknown or unsupported
    """
    provider = config.provider.lower()

    logger.info(f"Creating LLM service for provider: {provider}, model: {config.model}")

    if provider in ('gemini', 'gemma'):
        return GeminiLLMService(config)
    elif provider == 'local':
        return LocalLLMService(config)
    else:
        raise ValueError(
            f"Unknown LLM provider: {provider}. "
            f"Supported providers: gemini, gemma, local"
        )
