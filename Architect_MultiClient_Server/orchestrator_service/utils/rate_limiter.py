"""Client-side tokens-per-minute limiter for LLM providers with a hard external quota (e.g. Gemini)."""

import asyncio
import time

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

_WINDOW_SECONDS = 60.0


class TokenPerMinuteLimiter:
    """Throttles callers so total estimated tokens sent in any trailing 60s window stays under budget."""

    def __init__(self, tokens_per_minute: int):
        self._tokens_per_minute = tokens_per_minute
        self._usage: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()

    async def acquire(self, estimated_tokens: int) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                cutoff = now - _WINDOW_SECONDS
                self._usage = [(t, n) for t, n in self._usage if t > cutoff]
                used = sum(n for _, n in self._usage)

                if used + estimated_tokens <= self._tokens_per_minute or not self._usage:
                    self._usage.append((now, estimated_tokens))
                    return

                wait_time = max((self._usage[0][0] + _WINDOW_SECONDS) - now, 0.1)
                logger.info(
                    f"Token budget: {used}/{self._tokens_per_minute} used this minute, "
                    f"waiting {wait_time:.1f}s before sending ~{estimated_tokens} more tokens"
                )
                await asyncio.sleep(wait_time)


_limiters: dict[str, TokenPerMinuteLimiter] = {}


def get_rate_limiter(model: str, tokens_per_minute: int) -> TokenPerMinuteLimiter:
    limiter = _limiters.get(model)
    if limiter is None:
        limiter = TokenPerMinuteLimiter(tokens_per_minute)
        _limiters[model] = limiter
    return limiter


def estimate_tokens(text: str) -> int:
    # Rough pre-flight estimate only (no exact tokenizer available without an extra API
    # call); kept conservative since Vietnamese/mixed-script text tokenizes denser than English.
    return max(1, len(text) // 3)
