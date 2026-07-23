from tenacity import RetryCallState
from tenacity.wait import wait_base


class WaitCustomStrategy(wait_base):
    """
    Custom wait strategy that:
    - Waits 1s between attempts within the same cycle (group of 3 attempts)
    - Waits 60s after the first cycle (attempt 3)
    - Waits 70s after the second cycle (attempt 6)
    - Waits 80s after the third cycle (attempt 9), etc.
    """

    def __call__(self, retry_state: RetryCallState) -> float:
        attempt = retry_state.attempt_number
        if attempt % 3 != 0:
            return 1.0

        cycle = attempt // 3
        return 60.0 + (cycle - 1) * 10.0
