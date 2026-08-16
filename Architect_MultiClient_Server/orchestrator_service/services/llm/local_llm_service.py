from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.services.llm.base_llm_service import BaseLLMService

T = TypeVar("T", bound=BaseModel)


class LocalLLMService(BaseLLMService):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        clean_url = config.base_url.replace("/chat/completions", "").rstrip("/") if config.base_url else ""
        self.client = AsyncOpenAI(base_url=clean_url or None, api_key=config.api_key)

    async def generate(
        self, prompt: str, response_model: type[T], model: str, temperature: float, top_p: float, timeout: int
    ) -> T:
        response = await self.client.beta.chat.completions.parse(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            response_format=response_model,
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        parsed_result = response.choices[0].message.parsed

        if parsed_result is None:
            raise ValueError("Empty response text from Local LLM")

        return parsed_result
