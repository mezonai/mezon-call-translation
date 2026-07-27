import json
import logging
import uuid
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
from orchestrator_service.models.summary_models import LightSummaryResult
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.prompt import build_light_summary_prompt
from orchestrator_service.services.postgresql.pg_summary_repository import PgSummaryRepository
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.participant_identity import (
    generate_participant_alias_maps,
    group_next_focus_by_user,
    mask_messages,
    sanitize_and_decode_list,
)
from orchestrator_service.utils.retry_utils import WaitCustomStrategy
from orchestrator_service.utils.summary_utils import parse_timestamp_to_seconds

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)

class LightSummaryService:
    def __init__(self, pg_repo: PgSummaryRepository, llm_service: BaseLLMService):
        self.pg_repo = pg_repo
        self.llm_service = llm_service
        self.config = get_config().light_summary

    async def _call_llm(self, prompt: str, response_model: type[T]) -> T:
        @retry(
            stop=stop_after_attempt(self.config.retry_count * 3),
            wait=WaitCustomStrategy(),
            retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
            before_sleep=before_sleep_log(logger, logging.ERROR),
            reraise=True,
        )
        async def _inner() -> T:
            return await self.llm_service.generate(
                prompt=prompt,
                response_model=response_model,
                model=self.config.model,
                temperature=self.config.temperature,
                top_p=self.config.top_p,
                timeout=self.config.timeout,
            )

        return await _inner()

    async def process_room( # type: ignore[explicit-any]
        self,
        room_id: str,
        room_name: str,
        messages: list[dict[str, Any]],
        id_to_alias: dict[str, str] | None = None,
        alias_to_id: dict[str, str] | None = None,
        language: str = "Vietnamese",
        target_duration: int | None = None,
        resume_from_section: int = 0,
    ) -> int:
        id_to_alias = id_to_alias or {}
        alias_to_id = alias_to_id or {}

        real_messages = messages
        working_messages = mask_messages(messages, id_to_alias)


        start_idx = 0
        section_index = 1
        previous_context = ""

        base_duration = target_duration if target_duration is not None else self.config.target_duration_min
        extend_min = self.config.extend_min
        max_duration = self.config.max_duration_min

        logger.info(
            f"Start processing light summary room_id={room_id}, room_name={room_name}, messages={len(working_messages)}"
        )

        if resume_from_section > 0:
            existing_sections = await self.pg_repo.get_section_summaries_by_room_id(room_id)
            if existing_sections:
                last_section = existing_sections[-1]

                for idx, msg in enumerate(working_messages):
                    if msg.get("timestamp") == last_section.end_time:
                        start_idx = idx + 1
                        break
                section_index = last_section.section_index + 1

                masked_prev = mask_messages(last_section.messages or [], id_to_alias)
                previous_context = json.dumps(masked_prev, ensure_ascii=False, indent=2)
                logger.info(
                    f"Resuming from section {section_index}, start_idx={start_idx}, previous_context loaded from DB"
                )

        while start_idx < len(working_messages):
            current_duration = base_duration

            while current_duration <= max_duration:
                candidate_end_idx = self.get_candidate_end_idx(
                    messages=working_messages, start_idx=start_idx, duration_min=current_duration
                )

                if len(working_messages) - candidate_end_idx < 5:
                    candidate_end_idx = len(working_messages)
                    candidate_messages = working_messages[start_idx:]
                else:
                    candidate_messages = working_messages[start_idx:candidate_end_idx]

                transcript_str = json.dumps(candidate_messages, ensure_ascii=False, indent=2)

                try:
                    is_final = (candidate_end_idx == len(working_messages))
                    prompt = build_light_summary_prompt(
                        conversation_str=transcript_str,
                        previous_context=previous_context,
                        language=language,
                        is_final_section=is_final
                    )
                    summary_result = await self._call_llm(prompt, LightSummaryResult)

                    if summary_result.key_discussions:
                        summary_result.key_discussions = sanitize_and_decode_list(summary_result.key_discussions, alias_to_id, require_brackets=True)
                    if summary_result.next_focus:
                        summary_result.next_focus = sanitize_and_decode_list(summary_result.next_focus, alias_to_id, require_brackets=True)
                    if summary_result.detail:
                        summary_result.detail = sanitize_and_decode_list(summary_result.detail, alias_to_id, require_brackets=False)

                except Exception as api_err:
                    logger.error(
                        f"LLM Error after all retries at start_idx={start_idx} room={room_id}. Error: {api_err}"
                    )
                    raise ValueError(f"Failed to process section due to LLM error: {api_err}") from api_err

                end_message_time = summary_result.end_message_time

                if end_message_time is not None:
                    break

                if candidate_end_idx == len(working_messages):
                    logger.info("Reached the end of index; forcing completion of the final section.")
                    break

                logger.info(f"Topic incomplete within {current_duration} mins, extending window...")
                current_duration += extend_min

            if not summary_result or (summary_result.end_message_time is None and candidate_end_idx < len(working_messages)):
                raise ValueError(f"Cannot find completed topic from start_idx={start_idx}")

            if candidate_end_idx == len(working_messages):
                end_idx = len(working_messages) - 1
            else:
                assert summary_result.end_message_time is not None
                end_idx = self.find_end_idx_by_time(
                    messages=working_messages,
                    start_idx=start_idx,
                    candidate_end_idx=candidate_end_idx,
                    end_message_time=summary_result.end_message_time,
                )

            section_messages = real_messages[start_idx : end_idx + 1]

            summary_to_save = {
                "context": summary_result.context or "",
                "key_discussions": summary_result.key_discussions or [],
                "next_focus": group_next_focus_by_user(summary_result.next_focus) if summary_result.next_focus else {},
                "detail": summary_result.detail or [],
            }

            masked_section_messages = mask_messages(section_messages, id_to_alias)
            previous_context = json.dumps(masked_section_messages, ensure_ascii=False, indent=2)

            section_start_time = section_messages[0]["timestamp"]
            section_end_time = section_messages[-1]["timestamp"]

            record = {
                "id": uuid.uuid4(),
                "room_id": uuid.UUID(room_id),
                "room_name": room_name,
                "section_index": section_index,
                "messages": section_messages,
                "summary_data": summary_to_save,
                "start_time": section_start_time,
                "end_time": section_end_time,
            }

            saved = await self.pg_repo.upsert_room_section_summary(record)
            if not saved:
                logger.error(f"Failed to save section summary room_id={room_id}, section_index={section_index}")
                raise RuntimeError(f"Failed to save section {section_index}")

            logger.info(
                f"Saved section summary room_id={room_id}, section_index={section_index}, "
                f"messages={start_idx}->{end_idx}, time={section_start_time}->{section_end_time}"
            )

            section_index += 1
            start_idx = end_idx + 1

        logger.info(f"Completed light summary room_id={room_id}, sections={section_index - 1}")

        return section_index - 1

    async def process_room_by_id(
        self,
        room_id: str,
        language: str = "Vietnamese",
        target_duration: int | None = None,
        resume_from_section: int = 0,
    ) -> int:
        summary, room = await self.pg_repo.get_summary_by_room_id(room_id)
        if not room:
            logger.error(f"Room summary not found: {room_id}")
            raise ValueError(f"Not found room: {room_id}")

        if not summary:
            logger.error(f"RoomSummary doc not found for room: {room_id}")
            raise ValueError(f"Not found room summary doc for room: {room_id}")

        messages = summary.messages
        id_to_alias, alias_to_id = generate_participant_alias_maps(summary.participants or [])

        if resume_from_section == 0:
            await self.pg_repo.delete_section_summaries_by_room_id(room_id)

        return await self.process_room(
            room_id=room_id,
            room_name=room.room_name or "Unknow",
            messages=messages or [],
            id_to_alias=id_to_alias,
            alias_to_id=alias_to_id,
            language=language,
            target_duration=target_duration,
            resume_from_section=resume_from_section,
        )

    def get_candidate_end_idx(self, messages: list[dict[Any, Any]], start_idx: int, duration_min: int) -> int: # type: ignore[explicit-any]
        start_time = parse_timestamp_to_seconds(messages[start_idx]["timestamp"])
        target_sec = start_time + duration_min * 60

        end_idx = start_idx

        while end_idx < len(messages) and parse_timestamp_to_seconds(messages[end_idx]["timestamp"]) <= target_sec:
            end_idx += 1

        return min(end_idx, len(messages))

    def find_end_idx_by_time( # type: ignore[explicit-any]
        self, messages: list[dict[str, Any]], start_idx: int, candidate_end_idx: int, end_message_time: str
    ) -> int:
        for idx in range(candidate_end_idx - 1, start_idx - 1, -1):
            if messages[idx].get("timestamp") == end_message_time:
                return idx

        raise ValueError(
            f"Cannot find end_message_time={end_message_time} "
            f"in candidate messages {start_idx}->{candidate_end_idx - 1}"
        )
