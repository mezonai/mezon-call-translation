import json
import uuid
from typing import Any, Dict, List

from orchestrator_service.services.postgresql.pg_summary_repository import PgSummaryRepository
# from orchestrator_service.services.postgresql.pg_transcript_repository import PgTranscriptRepository
from orchestrator_service.services.llm.prompt import build_light_summary_prompt, build_overall_context_prompt
from orchestrator_service.models.summary_models import LightSummaryResult, OverallContextResult
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.utils.summary_utils import parse_timestamp_to_seconds
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.config.application_config import get_config
from pydantic import BaseModel
import asyncio

logger = get_logger(__name__)

class LightSummaryService:
    def __init__(self, pg_repo: PgSummaryRepository, llm_service: BaseLLMService):
        self.pg_repo = pg_repo
        self.llm_service = llm_service
        self.config = get_config().light_summary
        
    async def _call_llm_with_retry(self, prompt: str, response_model: type[BaseModel]) -> BaseModel:
        for attempt in range(1, self.config.retry_count + 1):
            try:
                return await self.llm_service.generate(
                    prompt=prompt,
                    response_model=response_model,
                    model=self.config.model,
                    timeout=self.config.timeout
                )
            except Exception as e:
                logger.error(f"[Light Summary LLM] attempt {attempt}/{self.config.retry_count} failed: {e}")
                if attempt == self.config.retry_count:
                    raise e
                await asyncio.sleep(2)

    
    async def process_room(
            self,
            room_id: str,
            room_name: str,
            messages: List[Dict],
            language: str = "Vietnamese",
            target_duration: int | None = None
    ):
        working_messages = messages

        start_idx = 0
        section_index = 1
        previous_context = ""

        base_duration = target_duration if target_duration is not None else self.config.target_duration_min
        extend_min = self.config.extend_min
        max_duration = self.config.max_duration_min

        logger.info(f"Start processing light summary room_id={room_id}, room_name={room_name}, messages={len(working_messages)}")

        await self.pg_repo.delete_section_summaries_by_room_id(room_id)


        while start_idx < len(working_messages):
            current_duration = base_duration

            while current_duration <= max_duration:
                candidate_end_idx = self.get_candidate_end_idx(
                    messages=working_messages,
                    start_idx=start_idx,
                    duration_min=current_duration
                )

                candidate_messages = working_messages[start_idx:candidate_end_idx]
                transcript_str = json.dumps(candidate_messages, ensure_ascii=False, indent=2)

                try:
                    prompt = build_light_summary_prompt(transcript_str, previous_context, language)
                    summary_result = await self._call_llm_with_retry(prompt, LightSummaryResult)
                except Exception as api_err:
                    logger.error(f"LLM Error after all retries at start_idx={start_idx} room={room_id}. Error: {api_err}")
                    raise ValueError(f"Failed to process section due to LLM error: {api_err}")

                end_message_time = summary_result.end_message_time

                if end_message_time is not None:
                    break

                logger.info(f"Topic incomplete within {current_duration} mins, extending window...")
                current_duration += extend_min

            if not summary_result or summary_result.end_message_time is None:
                raise ValueError(f"Cannot find completed topic from start_idx={start_idx}")
            
            end_idx = self.find_end_idx_by_time(
                messages=working_messages,
                start_idx=start_idx,
                candidate_end_idx=candidate_end_idx,
                end_message_time=summary_result.end_message_time
            )
            
            section_messages = working_messages[start_idx:end_idx + 1]

            summary_to_save = {
                "context": summary_result.context or "",
                "key_discussions": summary_result.key_discussions or [],
                "next_focus": summary_result.next_focus or [],
                "detail": summary_result.detail or []
            }

            previous_context = json.dumps(section_messages, ensure_ascii=False, indent=2)

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
                "end_time": section_end_time
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
            target_duration: int | None = None
    ) -> Dict[str, Any]:
        summary, room = await self.pg_repo.get_summary_by_room_id(room_id)
        if not room:
            logger.error(f"Room summary not found: {room_id}")
            raise ValueError(f"Not found room: {room_id}")
        
        if not summary:
            logger.error(f"RoomSummary doc not found for room: {room_id}")
            raise ValueError(f"Not found room summary doc for room: {room_id}")
        
        messages = summary.messages

        await self.process_room(
            room_id=room_id,
            room_name=room.room_name,
            messages=messages,
            language=language,
            target_duration=target_duration
        )

        final_summary = await self.generate_overall_summary(room_id, language)
        await self.pg_repo.update_room_summary_data(room_id, final_summary)

        return final_summary
    
    def get_candidate_end_idx(
            self,
            messages: List[Dict],
            start_idx: int,
            duration_min: int
    ) -> int:
        start_time = parse_timestamp_to_seconds(messages[start_idx]["timestamp"])
        target_sec = start_time + duration_min * 60

        end_idx = start_idx

        while (end_idx < len(messages) and parse_timestamp_to_seconds(messages[end_idx]["timestamp"]) <= target_sec):
            end_idx += 1

        return min(end_idx, len(messages))
    
    def find_end_idx_by_time(self, messages: List[Dict], start_idx: int, candidate_end_idx: int, end_message_time: str ) -> int:
        for idx in range(candidate_end_idx - 1, start_idx -1, -1):
            if messages[idx].get("timestamp") == end_message_time:
                return idx
            
        raise ValueError(
            f"Cannot find end_message_time={end_message_time} "
            f"in candidate messages {start_idx}->{candidate_end_idx - 1}"
        )
    
    def merge_section_summaries(self, sections: List[Dict], overall_context: str) -> Dict:
        final_summary = {
            "context": overall_context,
            "key_discussions": [],
            "next_focus": [],
            "detail": []
        }

        for sec in sections:
            summary = sec.summary_data or {}
            
            for key in ["key_discussions", "next_focus", "detail"]:
                items = summary.get(key, [])
                if isinstance(items, list):
                    final_summary[key].extend(items)

        return final_summary
    
    async def generate_overall_summary(self, room_id: str, language: str = "Vietnamese") -> Dict[str, Any]:
        sections = await self.pg_repo.get_section_summaries_by_room_id(room_id)

        if not sections:
            logger.error(f"No section summaries found for room_id={room_id}")
            raise ValueError(f"No section summaries found for room_id={room_id}")

        
        section_context = [
            {
                "section_index": sec.section_index,
                "start_time": sec.start_time,
                "end_time": sec.end_time,
                "context": (sec.summary_data or {}).get("context", "") 
            }
            for sec in sections
        ]

        try:
            section_context_str = json.dumps(section_context, ensure_ascii=False, indent=2)
            prompt = build_overall_context_prompt(section_context_str, language)
            result = await self._call_llm_with_retry(prompt, OverallContextResult)

            return self.merge_section_summaries(
            sections=sections,
            overall_context=result.context
            )
        except Exception as e:
            logger.error(f"Failed to generate overall summary for room_id={room_id}: {e}")
            raise ValueError(f"Failed to generate overall summary for room_id={room_id}: {e}")