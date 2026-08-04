"""Shared retry helper (PLAN.md D5 tier 1 / D8) used by the use cases below."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from record_service.domain.policies import RetryPolicy

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def with_retry(policy: RetryPolicy, op_name: str, func: Callable[[], Awaitable[T]]) -> T:
    """Runs func() with exponential backoff. Re-raises the last error once
    max_attempts is exhausted -- the caller decides what "exhausted" means
    (tier 2/3 fallback, mark session failed, etc.), this helper only retries.
    """
    last_exc: Exception | None = None
    for attempt in range(policy.max_attempts):
        try:
            return await func()
        except Exception as exc:  # noqa: BLE001 - intentionally broad, this is the retry boundary
            last_exc = exc
            if attempt < policy.max_attempts - 1:
                delay = policy.delay_for(attempt)
                logger.warning(
                    "%s failed (attempt %d/%d): %s -- retrying in %.2fs",
                    op_name,
                    attempt + 1,
                    policy.max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
    assert last_exc is not None
    raise last_exc
