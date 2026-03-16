import json
import re
from typing import Any, Dict, Optional

import httpx

from orchestrator_service.services.prompt import build_prompt_summary
from orchestrator_service.models.summary_models import SummaryActionItemsResult
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

def extract_json_from_llm(result: dict) -> Dict[str, Any]:
    """Safely extract JSON payload from OpenAI-compatible chat completion responses."""
    if "choices" not in result:
        logger.error("Invalid local LLM API response: missing 'choices'")
        logger.debug(json.dumps(result, indent=2, ensure_ascii=False))
        raise RuntimeError("Invalid API response")

    msg = result["choices"][0].get("message", {})
    raw = (msg.get("content") or msg.get("reasoning_content") or "").strip()

    if not raw:
        raise ValueError("Empty LLM output")

    # 1) Direct JSON parse
    try:
        return json.loads(raw)
    except Exception:
        logger.warning("Direct JSON parse failed, attempting to extract JSON from LLM output")
        pass

    # 2) Extract balanced JSON object candidates
    stack = []
    start = None
    candidates = []
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
            return json.loads(candidate)
        except Exception:
            continue

    # 3) Extract JSON in markdown code blocks
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except Exception:
            continue

    logger.error("Cannot extract JSON from local LLM output")
    raise ValueError("No valid JSON found in LLM response")


class LocalLLMService:
    """Service for interacting with NCC local OpenAI-compatible LLM API."""

    def __init__(
        self,
        base_url: str,
        model_name: str,
        api_key: Optional[str] = None,
        timeout: int = 120,
    ):
        self.base_url = base_url
        self.model_name = model_name
        self.api_key = api_key
        self.timeout = timeout

    def _build_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def summarize_conversation_local(self, conversation_text: str) -> SummaryActionItemsResult:
        """Summarize conversation using local LLM and return validated result."""
        prompt = build_prompt_summary(conversation_text)

        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
            "json_schema": SummaryActionItemsResult.model_json_schema(),
            "max_tokens": 15000,
            "temperature": 0,
        }

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.base_url,
                headers=self._build_headers(),
                json=payload,
            )
        response.raise_for_status()
        result = response.json()
        json_data = extract_json_from_llm(result)

        # Validate and parse JSON into SummaryActionItemsResult
        return SummaryActionItemsResult.model_validate(json_data)
