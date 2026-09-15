from typing import TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.utils.llm_utils import extract_json_from_llm

T = TypeVar("T", bound=BaseModel)


class MezonLLMService(BaseLLMService):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        clean_url = config.base_url.replace("/chat/completions", "").rstrip("/") if config.base_url else ""
        self.client = AsyncOpenAI(
            base_url=clean_url or None,
            api_key=config.api_key,
            default_headers={"User-Agent": "Mezon-Orchestrator/1.0"}
        )

    async def generate(
        self, prompt: str, response_model: type[T], model: str, temperature: float, top_p: float, timeout: int
    ) -> T:
        response = await self.client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            top_p=top_p,
            timeout=timeout,
        )
        raw_text = response.choices[0].message.content

        if not raw_text:
            raise ValueError("Empty response text from Mezon LLM")

        parsed_json = extract_json_from_llm(raw_text)
        return response_model.model_validate(parsed_json)
