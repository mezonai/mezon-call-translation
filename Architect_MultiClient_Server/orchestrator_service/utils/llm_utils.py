import json
import logging
import re
from typing import Any

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


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

    # Remove null
    raw = raw.replace("\x00", "")

    # 1) Direct JSON parse
    try:
        return json.loads(raw, strict=False)  # type: ignore[no-any-return]
    except Exception:
        logger.warning(f"Direct JSON parse failed, attempting to extract JSON from LLM output: {raw}")
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
            return json.loads(candidate, strict=False)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Candidate JSON parse failed, trying next candidate")
            continue

    # 3) Extract JSON in markdown code blocks
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", raw, re.DOTALL)
    for block in reversed(blocks):
        try:
            return json.loads(block, strict=False)  # type: ignore[no-any-return]
        except Exception:
            logger.warning("Markdown code block JSON parse failed, trying next block")
            continue

    logger.error("Cannot extract JSON from LLM output")
    raise ValueError("No valid JSON found in LLM response")


def create_retry_logger(
    target_logger: logging.Logger,
    room_id: str | None = None,
    max_attempts: int | None = None,
    action: str = "LLM call",
):
    """
    Creates a tenacity before_sleep callback that includes room_id,
    attempt count, sleep duration, and clean exception information.
    """
    def _log_retry(retry_state) -> None:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        sleep_sec = retry_state.next_action.sleep if retry_state.next_action else 0
        attempt = retry_state.attempt_number
        attempt_str = f"{attempt}/{max_attempts}" if max_attempts else f"{attempt}"
        room_tag = f"[Room: {room_id}] " if room_id else ""
        target_logger.error(
            f"{room_tag}Retrying {action} (attempt {attempt_str}) in {sleep_sec:.1f}s "
            f"due to {type(exc).__name__}: {exc}"
        )

    return _log_retry
