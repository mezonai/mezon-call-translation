"""
Service for correcting spelling and grammatical errors in room transcripts using an LLM.
"""

import logging
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
)

from orchestrator_service.config.application_config import get_config
from orchestrator_service.constants.exceptions import RETRYABLE_EXCEPTIONS
from orchestrator_service.models.transcript_models import TranscriptCorrectionResult, TranscriptCorrectionRetryType
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.llm_factory import create_llm_service
from orchestrator_service.services.llm.prompt import build_transcript_correction_prompt
from orchestrator_service.services.postgresql.pg_summary_repository import (
    PgSummaryRepository,
    get_pg_summary_repository,
)
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.retry_utils import WaitCustomStrategy
from orchestrator_service.utils.summary_utils import parse_timestamp_to_seconds

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class TranscriptCorrectionService:
    """Service to handle transcript correction logic"""

    def __init__(
        self,
        pg_summary_repo: PgSummaryRepository,
        llm_service: BaseLLMService,
        llm_service_fallback: BaseLLMService | None = None,
    ):
        self.pg_summary_repo = pg_summary_repo
        self.config = get_config().transcript_correction
        self.llm_service = llm_service
        self.llm_service_fallback = llm_service_fallback
        logger.info(f"TranscriptCorrectionService initialized with LLM provider: {self.config.provider}")

    async def _call_llm(
        self,
        llm_service: BaseLLMService,
        prompt: str,
        response_model: type[T],
        model: str,
        timeout: int,
        temperature: float,
        top_p: float,
        max_attempts: int,
    ) -> T:
        @retry(
            stop=stop_after_attempt(max_attempts * 3),
            wait=WaitCustomStrategy(),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.ERROR),
            reraise=True,
        )
        async def _inner() -> T:
            return await llm_service.generate(
                prompt=prompt,
                response_model=response_model,
                model=model,
                temperature=temperature,
                top_p=top_p,
                timeout=timeout,
            )

        return await _inner()

    async def _call_llm_with_fallback(self, prompt: str, response_model: type[T]) -> T:
        try:
            return await self._call_llm(
                llm_service=self.llm_service,
                prompt=prompt,
                response_model=response_model,
                model=self.config.model,
                temperature=self.config.temperature,
                top_p=self.config.top_p,
                timeout=self.config.timeout,
                max_attempts=self.config.retry_count,
            )
        except Exception as e:
            if self.config.fallback_enable and self.llm_service_fallback:
                logger.warning(f"All primary attempts failed ({e}). Switching to fallback LLM.")
                return await self._call_llm(
                    llm_service=self.llm_service_fallback,
                    prompt=prompt,
                    response_model=response_model,
                    model=self.config.fallback_model,
                    temperature=self.config.fallback_temperature,
                    top_p=self.config.fallback_top_p,
                    timeout=self.config.fallback_timeout,
                    max_attempts=self.config.fallback_retry_count,
                )
            raise

    def get_candidate_end_idx(self, messages: list[dict[str, Any]], start_idx: int, duration_min: int) -> int:  # type: ignore[explicit-any]
        """Find the index where the time difference from start_idx is exactly duration_min."""
        start_time = parse_timestamp_to_seconds(messages[start_idx]["timestamp"])
        target_sec = start_time + duration_min * 60

        end_idx = start_idx
        while end_idx < len(messages) and parse_timestamp_to_seconds(messages[end_idx]["timestamp"]) <= target_sec:
            end_idx += 1

        return min(end_idx, len(messages))

    def _build_previous_context(self, messages: list[dict[str, Any]], end_idx: int) -> str:  # type: ignore[explicit-any]
        """Build previous context from the last N minutes of already-corrected messages."""
        if end_idx == 0:
            return ""

        end_time = parse_timestamp_to_seconds(messages[end_idx - 1]["timestamp"])
        target_start_sec = max(0, end_time - self.config.previous_context_min * 60)

        start_idx = end_idx - 1
        while start_idx >= 0 and parse_timestamp_to_seconds(messages[start_idx]["timestamp"]) >= target_start_sec:
            start_idx -= 1

        start_idx = max(0, start_idx)

        context_lines = []
        for i in range(start_idx, end_idx):
            content = messages[i].get("content", "").strip()
            if content:
                context_lines.append(f"[{i}] {content}")
        return "\n".join(context_lines)

    async def correct_transcript_for_room(  # type: ignore[explicit-any]
        self,
        room_id: str,
        retry_type: TranscriptCorrectionRetryType = TranscriptCorrectionRetryType.SECTION,
    ) -> list[dict[str, Any]]:
        """
        Correct spelling and grammar for the room transcript.
        Supports resume: reads correction_progress to skip already-corrected chunks,
        flushes messages + progress to DB after each successful chunk.
        """
        summary_doc, _room_doc = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        if not summary_doc:
            raise ValueError(f"Room summary not found for room_id: {room_id}")

        messages = summary_doc.messages
        if not messages:
            raise ValueError(f"No messages found for room_id: {room_id}")

        # Ensure we have a clean copy to update
        corrected_messages = list(messages)
        total_messages = len(corrected_messages)

        # ── Resume support ──────────────────────────────────────────────
        progress = summary_doc.correction_progress or {}
        resume_idx = 0

        if retry_type == TranscriptCorrectionRetryType.ALL:
            logger.info(f"Forcing correction from the beginning for room {room_id} (retry_type=all)")
        else:
            if progress.get("status") == "completed":
                logger.info(f"Transcript correction already completed for room {room_id}, skipping.")
                return corrected_messages

            if progress.get("status") == "in_progress" and isinstance(progress.get("last_corrected_idx"), int):
                resume_idx = progress["last_corrected_idx"] + 1
                if resume_idx >= total_messages:
                    logger.info(f"All messages already corrected for room {room_id}, marking completed.")
                    await self.pg_summary_repo.flush_correction_progress(
                        room_id, corrected_messages,
                        {"last_corrected_idx": total_messages - 1, "status": "completed",
                         "updated_at": datetime.now(UTC).isoformat()},
                    )
                    return corrected_messages
                logger.info(
                    f"Resuming transcript correction for room {room_id} from message idx {resume_idx} "
                    f"(skipping {resume_idx}/{total_messages} already-corrected messages)"
                )

        logger.info(f"Starting transcript correction for room {room_id} ({total_messages} messages, resume_idx={resume_idx})")

        start_idx = resume_idx
        while start_idx < total_messages:
            end_idx = self.get_candidate_end_idx(corrected_messages, start_idx, self.config.chunk_duration_min)

            # If start_idx == end_idx but we haven't reached the end, process at least 1 message
            if end_idx == start_idx:
                end_idx += 1

            chunk_lines = []
            for i in range(start_idx, end_idx):
                content = corrected_messages[i].get("content", "").strip()
                if content:
                    chunk_lines.append(f"[{i}] {content}")

            if not chunk_lines:
                start_idx = end_idx
                continue

            indexed_content = "\n".join(chunk_lines)
            previous_context = self._build_previous_context(corrected_messages, start_idx)

            logger.info(f"Correcting chunk {start_idx}->{end_idx - 1} for room {room_id}")

            try:
                prompt = build_transcript_correction_prompt(indexed_content, previous_context)
                result = await self._call_llm_with_fallback(prompt, TranscriptCorrectionResult)

                for entry in result.entries:
                    idx = entry.index
                    if start_idx <= idx < end_idx:
                        corrected_messages[idx]["content"] = entry.corrected_content
                    else:
                        logger.warning(f"LLM returned out-of-bounds index {idx} (expected {start_idx}-{end_idx - 1})")

            except Exception as e:
                logger.error(f"Failed to correct chunk {start_idx}->{end_idx - 1} for room {room_id}: {e}")
                raise

            # ── Flush progress after each successful chunk ──────────────
            chunk_progress = {
                "last_corrected_idx": end_idx - 1,
                "status": "in_progress",
                "updated_at": datetime.now(UTC).isoformat(),
            }
            flushed = await self.pg_summary_repo.flush_correction_progress(
                room_id, corrected_messages, chunk_progress,
            )
            if not flushed:
                logger.error(f"Failed to flush progress at chunk {start_idx}->{end_idx - 1} for room {room_id}")
                raise RuntimeError(f"DB flush failed at chunk ending idx {end_idx - 1}")

            logger.info(f"✅ Flushed chunk {start_idx}->{end_idx - 1} for room {room_id}")
            start_idx = end_idx

        # ── Mark completed ──────────────────────────────────────────────
        final_progress = {
            "last_corrected_idx": total_messages - 1,
            "status": "completed",
            "updated_at": datetime.now(UTC).isoformat(),
        }
        await self.pg_summary_repo.flush_correction_progress(
            room_id, corrected_messages, final_progress,
        )

        logger.info(f"✅ Successfully completed transcript correction for room {room_id}")
        return corrected_messages


# Get singleton instance
_correction_service: TranscriptCorrectionService | None = None


def get_correction_service() -> TranscriptCorrectionService:
    global _correction_service
    if _correction_service is None:
        config = get_config()
        pg_repo = get_pg_summary_repository()
        llm = create_llm_service(
            config.transcript_correction.provider,
            config.transcript_correction.model,
            config.transcript_correction.temperature,
            config.transcript_correction.top_p,
        )
        llm_fallback = None
        if config.transcript_correction.fallback_enable:
            llm_fallback = create_llm_service(
                config.transcript_correction.fallback_provider,
                config.transcript_correction.fallback_model,
                config.transcript_correction.fallback_temperature,
                config.transcript_correction.fallback_top_p,
            )
        _correction_service = TranscriptCorrectionService(
            pg_summary_repo=pg_repo,
            llm_service=llm,
            llm_service_fallback=llm_fallback
        )
    return _correction_service
