from tenacity import RetryCallState
from tenacity.wait import wait_base


class WaitCustomStrategy(wait_base):
    """
    Custom wait strategy that:
    - If the failed attempt carries a server-reported retry_delay_seconds (e.g. Gemini's
      429 RetryInfo.retryDelay, set by gemini_llm_service.py), waits that long instead of guessing.
    - Otherwise waits 1s between attempts within the same cycle (group of 3 attempts)
    - Waits 60s after the first cycle (attempt 3)
    - Waits 70s after the second cycle (attempt 6)
    - Waits 80s after the third cycle (attempt 9), etc.
    """

    def __call__(self, retry_state: RetryCallState) -> float:
        exc = retry_state.outcome.exception() if retry_state.outcome else None
        retry_delay = getattr(exc, "retry_delay_seconds", None)
        if isinstance(retry_delay, (int, float)):
            return retry_delay + 1.0

        attempt = retry_state.attempt_number
        if attempt % 3 != 0:
            return 1.0

        cycle = attempt // 3
        return 60.0 + (cycle - 1) * 10.0
