"""
Service for generating room summaries
"""

import json
import logging
from datetime import datetime
from typing import Any, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
)

from orchestrator_service.api.sse.channels.metadata_channel import MetadataChannel
from orchestrator_service.auth.authorization import AuthContext
from orchestrator_service.config.application_config import get_config
from orchestrator_service.constants.exceptions import RETRYABLE_EXCEPTIONS
from orchestrator_service.exceptions import SummaryRetryNotFoundError
from orchestrator_service.models.summary_models import (
    OverallContextResult,
    RetryType,
    RoomSummaryResponse,
    SummaryResult,
)
from orchestrator_service.services.light_summary_service import LightSummaryService
from orchestrator_service.services.llm.base_llm_service import BaseLLMService
from orchestrator_service.services.llm.llm_factory import create_llm_service
from orchestrator_service.services.llm.prompt import (
    build_overall_context_prompt,
    build_prompt_summary,
)
from orchestrator_service.services.postgresql.models import RoomSectionSummary, TranscriptChunk
from orchestrator_service.services.postgresql.pg_outbox_repository import PgOutboxRepository, get_pg_outbox_repository
from orchestrator_service.services.postgresql.pg_summary_repository import (
    PgSummaryRepository,
    get_pg_summary_repository,
)
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
    get_pg_transcript_repository,
)
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.participant_identity import (
    build_username_maps,
    format_key_discussions,
    group_next_focus_by_user,
    sanitize_and_decode_list,
)
from orchestrator_service.utils.retry_utils import WaitCustomStrategy
from orchestrator_service.utils.time_convert import convert_to_iso_8601

logger = get_logger(__name__)
T = TypeVar("T", bound=BaseModel)


class SummaryService:
    """Service to handle room summarization logic"""

    def __init__(
        self,
        pg_transcript_repo: PgTranscriptRepository,
        pg_summary_repo: PgSummaryRepository,
        outbox_repo: PgOutboxRepository,
        light_summary_service: LightSummaryService,
        llm_service: BaseLLMService,
        llm_service_fallback: BaseLLMService | None = None,
    ):
        self.pg_transcript_repo = pg_transcript_repo
        self.pg_summary_repo = pg_summary_repo
        self.outbox_repo = outbox_repo
        self.config = get_config().summary
        self.light_summary_service = light_summary_service
        self.llm_service = llm_service
        self.llm_service_fallback = llm_service_fallback
        logger.info(f"SummaryService initialized with LLM provider: {self.config.provider}")

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
                prompt=prompt, response_model=response_model, model=model, temperature=temperature, top_p=top_p, timeout=timeout
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

    def merge_section_summaries(self, sections: list[RoomSectionSummary], overall_context: str) -> dict[str, Any]: # type: ignore[explicit-any]
        raw_data: dict[str, Any] = {  # type: ignore[explicit-any]
            "key_discussions": [],
            "next_focus": {},
            "detail": []
        }

        for sec in sections:
            summary = sec.summary_data or {}

            for key in ["key_discussions", "detail"]:
                items = summary.get(key, [])
                if isinstance(items, list):
                    raw_data[key].extend(items)

            sec_next_focus = summary.get("next_focus", {})
            if isinstance(sec_next_focus, dict):
                for user_id, tasks in sec_next_focus.items():
                    if user_id not in raw_data["next_focus"]:
                        raw_data["next_focus"][user_id] = []
                    raw_data["next_focus"][user_id].extend(tasks)

        summary_parts = []
        if overall_context:
            summary_parts.append(f"Context\n{overall_context}")

        if raw_data["key_discussions"]:
            summary_parts.append("Key Discussions\n" + "\n".join(raw_data["key_discussions"]))

        return {
            "summary": "\n\n".join(summary_parts) if summary_parts else "",
            "action_items": raw_data["next_focus"],
            "detail": raw_data["detail"],
        }

    async def generate_overall_summary(self, room_id: str, language: str = "Vietnamese") -> dict[str, Any]:  # type: ignore[explicit-any]
        sections = await self.pg_summary_repo.get_section_summaries_by_room_id(room_id)

        if not sections:
            logger.error(f"No section summaries found for room_id={room_id}")
            raise ValueError(f"No section summaries found for room_id={room_id}")

        section_context = [
            {
                "section_index": sec.section_index,
                "start_time": sec.start_time,
                "end_time": sec.end_time,
                "context": (sec.summary_data or {}).get("context", ""),
            }
            for sec in sections
        ]

        try:
            section_context_str = json.dumps(section_context, ensure_ascii=False, indent=2)
            prompt = build_overall_context_prompt(section_context_str, language)
            result = await self._call_llm_with_fallback(prompt, OverallContextResult)

            return self.merge_section_summaries(sections=sections, overall_context=result.context)
        except Exception as e:
            logger.error(f"Failed to generate overall summary for room_id={room_id}: {e}")
            raise ValueError(f"Failed to generate overall summary for room_id={room_id}: {e}") from e

    async def retry_overall_summary_only(self, room_id: str, language: str = "Vietnamese") -> dict[str, Any]:  # type: ignore[explicit-any]
        """Chỉ retry generate_overall_summary, không chạy lại sections."""
        sections = await self.pg_summary_repo.get_section_summaries_by_room_id(room_id)
        if not sections:
            raise ValueError(f"No existing sections found for room_id={room_id}. Cannot retry overall only.")

        final_summary = await self.generate_overall_summary(room_id, language)
        await self.pg_summary_repo.update_room_summary_data(room_id, final_summary)

        return final_summary

    async def generate_summary(self, room_id: str) -> dict[str, Any] | None:  # type: ignore[explicit-any]
        """
        Generate a summary for the given room_id.

        Currently implements a concatenation strategy:
        1. Verify room exists.
        2. Get all tracks.
        3. Collect all segments.
        4. Sort by absolute time.
        5. Collect Full Text and Participants.
        6. Save transcript draft to DB.
        7. Generate Summary via LLM.
        8. Update summary in DB.
        9. Notify clients via SSE.
        """
        # 1. Verify room exists
        room_doc = await self.pg_transcript_repo.get_room_by_id(room_id)
        if not room_doc:
            logger.error(f"Generate Summary: Room not found for id: {room_id}")
            return None

        # 2. Get All Tracks
        tracks = await self.pg_transcript_repo.get_tracks_by_room(room_id, status="completed")
        if not tracks:
            logger.warning(f"No tracks found for room {room_id}")
            return None

        # 3. Collect all segments with absolute timestamps in one pass
        # TODO: Use `Any` type because the `type` data to insert into `all_segments` has unknown type
        all_segments: list[dict[str, Any]] = []  # type: ignore[explicit-any]
        participant_durations: dict[str, float] = {}

        track_ids = [str(track.id) for track in tracks]
        all_chunks = await self.pg_transcript_repo.get_chunks_by_track_ids(track_ids, sorted_by_index=True)

        chunks_by_track: dict[str, list[TranscriptChunk]] = {tid: [] for tid in track_ids}
        for chunk in all_chunks:
            tid = str(chunk.track_ref_id)
            if tid in chunks_by_track:
                chunks_by_track[tid].append(chunk)

        for track in tracks:
            try:
                participant = track.participant_identity or "Unknown"
                audio_info = track.audio_info or {}
                track_start_ns = int(audio_info.get("started_at_ns", 0) or 0)
                chunks = chunks_by_track[track.id]

                vad_duration = audio_info.get("duration_after_vad_sec")
                if vad_duration is not None:
                    track_duration = float(vad_duration)
                else:
                    track_duration = sum((c.end_time or 0.0) - (c.start_time or 0.0) for c in chunks)
                if participant != "Unknown":
                    participant_durations[participant] = participant_durations.get(participant, 0.0) + track_duration

                for chunk in chunks:
                    for seg in chunk.segments or []:
                        text = seg.get("text", "").strip()
                        if not text:
                            continue

                        seg_start_ns = track_start_ns + int((seg.get("start") or 0.0) * 1_000_000_000)

                        all_segments.append(
                            {
                                "timestamp": seg_start_ns,
                                "participant_id": participant,
                                "text": text,
                            }
                        )
            except Exception as e:
                logger.error(f"Error processing track {track.id}: {e}")
                continue

        if not all_segments:
            logger.warning(f"No transcript segments found for room {room_id}")
            return None

        all_segments.sort(key=lambda x: x["timestamp"])

        # 4. Collect Full Text and Participants
        unique_participants = {seg["participant_id"] for seg in all_segments}
        room_participants_raw = room_doc.participants
        # TODO: Use `Any` type because the `room_participant_raw` data to insert into `room_participants` has complex type
        room_participants: list[dict[str, Any]] = (  # type: ignore[explicit-any]
            room_participants_raw if isinstance(room_participants_raw, list) else []
        )
        id_to_username, username_to_id = build_username_maps(list(unique_participants), room_participants)

        turns = []

        for seg in all_segments:
            real_p = str(seg["participant_id"])
            text = seg["text"]
            if current_turn and current_turn["participant_id"] == real_p:
                current_turn["content"] += f"\n{text}"
            else:
                if current_turn:
                    turns.append(current_turn)
                dt = datetime.fromtimestamp(seg["timestamp"] / 1_000_000_000)
                current_turn = {
                    "timestamp": dt.strftime("%H:%M:%S"),
                    "participant_id": real_p,
                    "content": text,
                }
        if current_turn:
            turns.append(current_turn)

        for p in room_participants:
            user_id = p.get("participant_identity")
            if user_id:
                p["duration"] = round(participant_durations.get(user_id, 0.0), 2)

        try:
            await self.pg_transcript_repo.update_room_participants(room_id, room_participants)
        except Exception as e:
            logger.error(f"Failed to save speech durations to database: {e}")

        # full_text is used for LLM summarization.
        # It can be a long string, but we keep it as is for now since it's needed for the summary generation step.
        # In the future, we could consider storing it in a more efficient way if we find performance issues with very long conversations.
        full_text = "\n".join(
            f"[{t['timestamp']}] {id_to_username.get(t['participant_id'], t['participant_id'])}: {t['content']}"
            for t in turns
        )

        draft_summary: dict[str, Any] = {  # type: ignore[explicit-any]
            "room_id": room_id,
            "room_name": room_doc.room_name or "Unknown",
            "participants": list(unique_participants),
            "summary_data": {},
            "messages": turns,
            "created_at": datetime.utcnow(),
            "total_segments": len(all_segments),
        }

        # 5. Save Summary to PostgreSQL
        try:
            saved_id = await self.pg_summary_repo.save_room_summary(draft_summary)
            if not saved_id:
                logger.error(f"Failed to save summary for room: {room_id}")
                return None
        except Exception as e:
            logger.error(f"Failed to save summary to DB: {e}")
            return None

        total_duration = 0.0
        if room_doc.created_at and room_doc.finalized_at:
            total_duration = (room_doc.finalized_at - room_doc.created_at).total_seconds()

        if total_duration > self.config.threshold_min * 60:
            logger.info(
                f"Room duration ({total_duration:.1f}s) > {self.config.threshold_min} mins. Using Light Summary flow."
            )

            try:
                # Phase 1: Sections
                await self.light_summary_service.process_room_by_id(room_id, language=self.config.language)
            except Exception as e:
                logger.warning(f"Section processing failed for room {room_id}: {e}. Creating outbox task.")
                outbox_created = await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id), retry_type=RetryType.SECTIONS, error_msg=str(e)
                )
                if not outbox_created:
                    logger.critical(
                        f"Room {room_id} has NO summary AND outbox retry task creation FAILED "
                        f"(retry_type={RetryType.SECTIONS}) - needs manual re-trigger"
                    )
                return {**draft_summary, "id": saved_id}

            try:
                # Phase 2: Overall context
                summary_data = await self.generate_overall_summary(room_id, language=self.config.language)
            except Exception as e:
                logger.warning(f"Overall summary failed for room {room_id}: {e}. Creating outbox task.")
                outbox_created = await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id), retry_type=RetryType.OVERALL_CONTEXT, error_msg=str(e)
                )
                if not outbox_created:
                    logger.critical(
                        f"Room {room_id} has NO summary AND outbox retry task creation FAILED "
                        f"(retry_type={RetryType.OVERALL_CONTEXT}) - needs manual re-trigger"
                    )
                return {**draft_summary, "id": saved_id}

            # Save result to DB
            updated = await self.pg_summary_repo.update_room_summary_data(room_id, summary_data)
            if not updated:
                logger.error(f"Failed to update generated light summary for room {room_id}")
                outbox_created = await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id), retry_type=RetryType.OVERALL_CONTEXT, error_msg="Failed to save light summary to DB"
                )
                if not outbox_created:
                    logger.critical(
                        f"Room {room_id} has NO summary AND outbox retry task creation FAILED "
                        f"(retry_type={RetryType.OVERALL_CONTEXT}) - needs manual re-trigger"
                    )
                return {**draft_summary, "id": saved_id}

            # Success
            final_summary = dict(draft_summary)
            final_summary["summary_data"] = summary_data

            logger.info(f"Generated light summary for room {room_id} (ID: {saved_id})")

            result = dict(final_summary)
            result["id"] = saved_id

            metadata_channel = MetadataChannel()
            await metadata_channel.push_room_summary_done(room_id=room_id, room_name=(room_doc.room_name or "Unknown"))
            return result

        else:
            # Normal summary flow
            logger.info(
                f"Room duration ({total_duration:.1f}s) <= {self.config.threshold_min} mins. Using Normal Summary flow."
            )
            summary_data_result = None

            try:
                summary_prompt = build_prompt_summary(full_text, self.config.language)
                summary_data_result = await self._call_llm_with_fallback(summary_prompt, SummaryResult)
            except Exception as e:
                logger.warning(f"Summary task failed for room {room_id}: {e}")

            # If summary failed -> Outbox SUMMARY
            if not summary_data_result:
                outbox_created = await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id), retry_type=RetryType.SUMMARY, error_msg="Summary failed"
                )
                if not outbox_created:
                    logger.critical(
                        f"Room {room_id} has NO summary AND outbox retry task creation FAILED "
                        f"(retry_type={RetryType.SUMMARY}) - needs manual re-trigger"
                    )
                return {**draft_summary, "id": saved_id}

            # Concate data
            summary_parts = []
            if summary_data_result:
                summary_parts.append(f"Context\n{summary_data_result.context}")

                if summary_data_result.key_discussions:
                    summary_data_result.key_discussions = sanitize_and_decode_list(summary_data_result.key_discussions, {}, require_brackets=True)
                    summary_data_result.key_discussions = format_key_discussions(summary_data_result.key_discussions)
                    if summary_data_result.key_discussions:
                        summary_parts.append("Key Discussions\n" + "\n".join(summary_data_result.key_discussions))

                if summary_data_result.next_focus:
                    summary_data_result.next_focus = sanitize_and_decode_list(summary_data_result.next_focus, username_to_id, require_brackets=True)

                if summary_data_result.detail:
                    summary_data_result.detail = sanitize_and_decode_list(summary_data_result.detail, {}, require_brackets=False)

            summary_data = {
                "summary": "\n\n".join(summary_parts) if summary_parts else "",
                "action_items": group_next_focus_by_user(summary_data_result.next_focus) if summary_data_result else {},
                "detail": summary_data_result.detail if summary_data_result and summary_data_result.detail else []
            }

            final_summary = dict(draft_summary)
            final_summary["summary_data"] = summary_data

            # Save reuslt to DB
            updated = await self.pg_summary_repo.update_room_summary(room_id, summary_data)
            if not updated:
                logger.error(f"Failed to update generated summary for room {room_id}")
                return {**draft_summary, "id": saved_id}

            logger.info(f"Generated summary for room {room_id} (ID: {saved_id})")
            result = dict(final_summary)
            result["id"] = saved_id

            # Send notice to SSE
            if summary_data_result:
                # ALL pass
                metadata_channel = MetadataChannel()
                await metadata_channel.push_room_summary_done(
                    room_id=str(room_id), room_name=room_doc.room_name or "Unknown"
                )
            elif not summary_data_result:
                # Summary failed
                logger.warning(f"Summary failed for room {room_id}. Creating SUMMARY outbox task.")
                outbox_created = await self.outbox_repo.add_retry_summarization_task_to_outbox(
                    room_id=str(room_id), retry_type=RetryType.SUMMARY, error_msg="Summary generation failed"
                )
                if not outbox_created:
                    logger.critical(
                        f"Room {room_id} has NO summary AND outbox retry task creation FAILED "
                        f"(retry_type={RetryType.SUMMARY}) - needs manual re-trigger"
                    )

            return result

    # TODO: Use `Any` type because `summary_data` response from this function has complex type
    async def retry_summary_from_full_text(  # type: ignore[explicit-any]
        self, room_id: str, retry_type: RetryType = RetryType.SUMMARY
    ) -> dict[str, Any] | None:
        """
        Hotfix: re-run LLM summarization by rebuilding full_text from messages stored in rooms_summary.
        Used when LLM service fails in the first run and summary_data is missing.

        Returns:
            summary_data dict if successful, None if failed.

        Raises:
            SummaryRetryNotFoundError: If document not found or messages is empty.
        """
        summary_doc, room_doc = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        if not summary_doc:
            raise SummaryRetryNotFoundError(f"Not found summary_doc for room_id: {room_id}")
        if not room_doc:
            raise SummaryRetryNotFoundError(f"Not found room_doc for room_id: {room_id}")

        messages = summary_doc.messages or []
        if not messages:
            raise SummaryRetryNotFoundError(f"messages is empty for room_id: {room_id}")

        unique_participants = {t["participant_id"] for t in messages}
        room_participants = room_doc.participants if isinstance(room_doc.participants, list) else []
        id_to_username, username_to_id = build_username_maps(list(unique_participants), room_participants)

        full_text = "\n".join(
            f"[{t['timestamp']}] {id_to_username.get(str(t['participant_id']), str(t['participant_id']))}: {t['content']}"
            for t in messages
        )

        total_duration = 0.0
        if room_doc.created_at and room_doc.finalized_at:
            total_duration = (room_doc.finalized_at - room_doc.created_at).total_seconds()

        if total_duration > self.config.threshold_min * 60:
            # Light summary flow
            logger.info(f"Retrying room ({total_duration:.1f}s). Using Light Summary flow.")

            try:
                if retry_type == RetryType.OVERALL_CONTEXT:
                    summary_data = await self.retry_overall_summary_only(room_id, language=self.config.language)
                elif retry_type == RetryType.SECTIONS:
                    existing_sections = await self.pg_summary_repo.get_section_summaries_by_room_id(room_id)
                    resume_from = existing_sections[-1].section_index if existing_sections else 0

                    await self.light_summary_service.process_room_by_id(
                        room_id, language=self.config.language, resume_from_section=resume_from
                    )

                    summary_data = await self.generate_overall_summary(room_id, language=self.config.language)

                    await self.pg_summary_repo.update_room_summary_data(room_id, summary_data)
                else:  # RetryType.ALL
                    await self.light_summary_service.process_room_by_id(room_id, language=self.config.language)

                    summary_data = await self.generate_overall_summary(room_id, language=self.config.language)
                    await self.pg_summary_repo.update_room_summary_data(room_id, summary_data)

                metadata_channel = MetadataChannel()
                await metadata_channel.push_room_summary_done(
                    room_id=room_id, room_name=summary_doc.room_name or "Unknown"
                )
                return summary_data
            except Exception as e:
                logger.error(f"Failed to retry light summary for room {room_id}: {e}")
                return None

        else:
            # Normal summary flow
            logger.info(
                f"Retrying room ({total_duration:.1f}s) <= {self.config.threshold_min} mins. Using Normal Summary flow."
            )

            logger.info(f"Retrying LLM with type '{retry_type.value}' for room {room_id} ({len(full_text)} chars)")

            is_success = False
            try:
                if retry_type == RetryType.SUMMARY:
                    prompt = build_prompt_summary(full_text, self.config.language)
                    result = await self._call_llm_with_fallback(prompt, SummaryResult)

                    # Format summary with only non-empty fields
                    summary_parts = [f"Context\n{result.context}"]

                    if result.key_discussions:
                        result.key_discussions = sanitize_and_decode_list(result.key_discussions, {}, require_brackets=True)
                        result.key_discussions = format_key_discussions(result.key_discussions)
                        if result.key_discussions:
                            summary_parts.append("Key Discussions\n" + "\n".join(result.key_discussions))

                    if result.next_focus:
                        result.next_focus = sanitize_and_decode_list(result.next_focus, username_to_id, require_brackets=True)

                    if result.detail:
                        result.detail = sanitize_and_decode_list(result.detail, {}, require_brackets=False)

                    summary_data = {
                        "summary": "\n\n".join(summary_parts),
                        "action_items": group_next_focus_by_user(result.next_focus) if result else {},
                        "detail": result.detail if result and result.detail else []
                    }

                    is_success = True

                updated = await self.pg_summary_repo.update_room_summary(room_id, summary_data)
                logger.info(f"Updated summary for room {room_id}")
                if not updated:
                    logger.error(f"Failed to update summary for room {room_id}")
                    return None

                # Notify clients via SSE if summary generation is successful
                if is_success:
                    metadata_channel = MetadataChannel()
                    await metadata_channel.push_room_summary_done(
                        room_id=room_id, room_name=summary_doc.room_name or "Unknown"
                    )

                logger.info(
                    f"Successfully updated summary for room {room_id} and notified clients (success={is_success})"
                )
                return summary_data
            except Exception as e:
                logger.error(f"Failed to retry summary for room {room_id} with type '{retry_type.value}': {e}")
                return None

    async def get_summary_by_room_name(
        self, room_name: str, start_time: datetime | None, end_time: datetime | None, auth: AuthContext
    ) -> tuple[list[RoomSummaryResponse], int]:
        if auth.can_view_all_rooms:
            summaries, rooms = await self.pg_summary_repo.get_summary_by_room_name(room_name, start_time, end_time)
        else:
            summaries, rooms = await self.pg_summary_repo.get_summary_by_room_name(
                room_name, start_time, end_time, auth.user_id
            )

        if not summaries:
            return [], 0

        room_dict = {str(r.id): r for r in rooms}
        summary_models = []

        for summary in summaries:
            summary_dict = {c.name: getattr(summary, c.name) for c in summary.__table__.columns}
            room = room_dict.get(str(summary.room_id))
            summary_dict["created_at"] = room.created_at if room else None
            summary_dict["completed_at"] = room.completed_at if room else None

            if summary_dict.get("room_id") is not None:
                summary_dict["room_id"] = str(summary_dict["room_id"])
            if summary_dict.get("created_at") is not None:
                summary_dict["created_at"] = convert_to_iso_8601(summary_dict["created_at"])
            if summary_dict.get("completed_at") is not None:
                summary_dict["completed_at"] = convert_to_iso_8601(summary_dict["completed_at"])

            summary_models.append(RoomSummaryResponse.model_construct(**summary_dict))

        return summary_models, len(summary_models)

    async def get_summary_by_room_id(self, room_id: str, auth: AuthContext) -> RoomSummaryResponse:
        if not auth.can_view_all_rooms:
            has_access = await self.pg_transcript_repo.user_has_room_access(room_id, auth.user_id)
            if not has_access:
                logger.warning(f"User {auth.user_id} denied access to room statistics for {room_id}")
                raise HTTPException(status_code=403, detail="You don't have access to this room")

        summary, room = await self.pg_summary_repo.get_summary_by_room_id(room_id)
        if not summary or not room:
            return RoomSummaryResponse.model_construct()

        summary_dict = {c.name: getattr(summary, c.name) for c in summary.__table__.columns}
        summary_dict["created_at"] = room.created_at
        # bot/FE both read this field (confirmed in use, don't drop it --
        # RoomSummaryResponse already declares it, this ORM rewrite just
        # hadn't populated it yet from the Room row).
        summary_dict["finalized_at"] = room.finalized_at
        summary_dict["completed_at"] = room.completed_at

        if summary_dict.get("room_id") is not None:
            summary_dict["room_id"] = str(summary_dict["room_id"])
        if summary_dict.get("created_at") is not None:
            summary_dict["created_at"] = convert_to_iso_8601(summary_dict["created_at"])
        if summary_dict.get("finalized_at") is not None:
            summary_dict["finalized_at"] = convert_to_iso_8601(summary_dict["finalized_at"])
        if summary_dict.get("completed_at") is not None:
            summary_dict["completed_at"] = convert_to_iso_8601(summary_dict["completed_at"])

        speech_durations = []
        room_participants_raw = room.participants

        # TODO: Use `Any` type because the `room_participant_raw` data to insert into `room_participants` has complex type
        room_participants: list[dict[str, Any]] = (  # type: ignore[explicit-any]
            room_participants_raw if isinstance(room_participants_raw, list) else []
        )

        for p in room_participants:
            user_id = p.get("participant_identity")
            if user_id and not user_id.startswith("EG_"):
                speech_durations.append({"participant_identity": user_id, "duration": p.get("duration", 0.0)})

        summary_dict["speech_durations"] = speech_durations

        return RoomSummaryResponse.model_construct(**summary_dict)


# Get singleton instance
_summary_service: SummaryService | None = None


def get_summary_service() -> SummaryService:
    global _summary_service
    if _summary_service is None:
        config = get_config()
        pg_transcript_repo = get_pg_transcript_repository()
        pg_summary_repo = get_pg_summary_repository()

        primary_llm = create_llm_service(config.summary.provider, config.summary.model, config.summary.temperature, config.summary.top_p)

        fallback_llm = None
        if config.summary.fallback_enable:
            fallback_llm = create_llm_service(
                config.summary.fallback_provider, config.summary.fallback_model, config.summary.fallback_temperature, config.summary.fallback_top_p
            )

        light_summary_llm = create_llm_service(
            config.light_summary.provider, config.light_summary.model, config.light_summary.temperature, config.light_summary.top_p
        )
        light_summary_service = LightSummaryService(pg_summary_repo, light_summary_llm)
        _summary_service = SummaryService(
            pg_transcript_repo=pg_transcript_repo,
            pg_summary_repo=pg_summary_repo,
            outbox_repo=get_pg_outbox_repository(),
            light_summary_service=light_summary_service,
            llm_service=primary_llm,
            llm_service_fallback=fallback_llm,
        )
    return _summary_service
