"""Business rules as values, kept separate from the infra that enforces them.

E.g. "how long to wait for a reconnect" is a business decision (PLAN.md D5),
not a gRPC detail -- it belongs here, not in infra/grpc/ingest_server.py.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay_seconds: float = 0.2

    def delay_for(self, attempt: int) -> float:
        """attempt is 0-indexed."""
        return self.base_delay_seconds * (2**attempt)


@dataclass(frozen=True)
class RecordingPolicy:
    """PLAN.md D5 (recovery tiers) / D11 (sanity checks) / D12 (quality annotations)."""

    part_size_bytes: int = 8 * 1024 * 1024
    upload_retry: RetryPolicy = RetryPolicy()

    # D5 tier 2: grace window for an agent to reconnect with the same session key
    # after an abrupt (non-graceful) stream drop, before we finalize best-effort.
    grace_period_seconds: float = 45.0

    # D11: raw_bytes_received below (expected_bytes * tolerance) at stop time
    # gets flagged quality_warning instead of a clean `completed`.
    byte_rate_tolerance: float = 0.5

    # D12: cumulative drop_rate() above this triggers a quality annotation.
    # This never causes record-service to discard data or start a new session --
    # it only annotates; the decision to trim is made downstream by
    # audio-processing-service.
    drop_rate_warning_threshold: float = 0.1

    # D8/D11: reporting retry to orchestrator before falling back to durable
    # local state + reconciliation.
    report_retry: RetryPolicy = RetryPolicy(max_attempts=3, base_delay_seconds=0.5)
