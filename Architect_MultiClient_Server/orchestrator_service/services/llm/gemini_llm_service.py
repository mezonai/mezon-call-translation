import json
import re
from typing import Any

from google import genai
from pydantic import BaseModel

from orchestrator_service.config.application_config import LLMConfig
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


# TODO: Use `Any` type because `json.loads()` parses dynamic structures from LLM responses that are not pre-defined.
def extract_json_from_llm(raw_text: str) -> dict[str, Any]:  # type: ignore[explicit-any]
    """
    Safely extract JSON payload from OpenAI-compatible chat completion responses.

    Args:
        raw_text: Raw API response string

    Returns:
        Extracted JSON dictionary

    Raises:
        RuntimeError: If response format is invalid
        ValueError: If no valid JSON found in response
    """
    raw = raw_text.strip()
    if not raw:
        raise ValueError("Empty LLM output")

    # 1) Direct JSON parse
    try:
        return json.loads(raw)  # type: ignore[no-any-return]
    except Exception:
        logger.warning(f"Direct JSON parse failed, attempting to extract JSON from LLM outputL: {raw}")
        pass

    # 2) Extract balanced JSON object candidates
    stack: list[str] = []
    start = None
    candidates: list[str] = []
    for i, ch in enumerate(raw):
        if ch == "{":
            if not stack:
                start = i
            stack.append(ch)
        elif ch == "}":
            if stack:
                stack.pop()
                if not stack and start is not None:
                    candidates.append(raw[start : i + 1])

    for candidate in reversed(candidates):
        try:
            return json.loads(candidate)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Candidate JSON parse failed, trying next candidate")
            continue

    # 3) Extract JSON in markdown code blocks
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Markdown code block JSON parse failed, trying next block")
            continue

    logger.error("Cannot extract JSON from local LLM output")
    raise ValueError("No valid JSON found in LLM response")


class GeminiLLMService(BaseLLMService):
    def __init__(self, config: LLMConfig):
        super().__init__(config)
        self.client = genai.Client(api_key=config.api_key)

    async def generate(
        self, prompt: str, response_model: type[BaseModel], model: str, temperature: float, timeout: int
    ) -> BaseModel:
        response = await self.client.aio.models.generate_content(
            model=model,
            contents=prompt,
            config={
                "response_mime_type": "application/json",
                "response_json_schema": response_model.model_json_schema(),
                "temperature": temperature,
            },
        )
        if response.text is None:
            raise ValueError("Empty response text from Gemini API")
        parsed_json = extract_json_from_llm(response.text)
        return response_model.model_validate(parsed_json)
