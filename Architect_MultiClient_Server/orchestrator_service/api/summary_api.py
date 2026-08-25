"""
Internal API endpoints for room summary
"""

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from orchestrator_service.models.dispatch_models import RoomIdPath
from orchestrator_service.models.summary_models import (
    RoomSummaryResponse,
    SummaryDetailResponse,
    SummaryListQuery,
    SummaryListResponse,
    SummaryRetryRequest,
    SummaryRetryResponse,
)
from orchestrator_service.services.postgresql.pg_summary_repository import (
    PgSummaryRepository,
)
from orchestrator_service.services.summary_service import get_summary_service
from orchestrator_service.utils.time_convert import convert_to_iso_8601
from orchestrator_service.utils.transcript_validators import RoomNamePath

client_router = APIRouter(prefix="/api/summary", tags=["Summary"])


@client_router.get("/room/{room_name}", response_model=SummaryListResponse, response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: RoomNamePath,
    query: SummaryListQuery = Query(),
) -> SummaryListResponse:
    """
    Get summary by room name.
    """
    pg_repo = PgSummaryRepository()
    start_time = query.start_time
    end_time = query.end_time

    summaries, rooms = await pg_repo.get_summary_by_room_name(room_name, start_time, end_time)

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

    return SummaryListResponse(
        status="ok",
        data=summary_models,
        count=len(summary_models),
    )

@client_router.get("/room/id/{room_id}", response_model=SummaryDetailResponse, response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: RoomIdPath,
) -> SummaryDetailResponse:
    """
    Get summary by room id.
    """
    pg_repo = PgSummaryRepository()
    summary, room = await pg_repo.get_summary_by_room_id(str(room_id))

    if not summary or not room:
        return SummaryDetailResponse(status="ok", data=RoomSummaryResponse())

    summary_dict = {c.name: getattr(summary, c.name) for c in summary.__table__.columns}
    summary_dict["created_at"] = room.created_at
    summary_dict["completed_at"] = room.completed_at

    if summary_dict.get("room_id") is not None:
        summary_dict["room_id"] = str(summary_dict["room_id"])
    if summary_dict.get("created_at") is not None:
        summary_dict["created_at"] = convert_to_iso_8601(summary_dict["created_at"])
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

    response_data = RoomSummaryResponse.model_construct(**summary_dict)

    return SummaryDetailResponse(status="ok", data=response_data)


@client_router.post(
    "/retry/{room_id}",
    response_model=SummaryRetryResponse,
    response_description="Re-run LLM summary using existing full_text",
)
async def retry_summary(
    room_id: RoomIdPath,
    request: SummaryRetryRequest,
) -> SummaryRetryResponse:
    summary_data = await get_summary_service().retry_summary_from_full_text(str(room_id), retry_type=request.type)

    if summary_data is None:
        raise HTTPException(status_code=500, detail="Update summary_data to DB failed")

    return SummaryRetryResponse(status="ok", room_id=str(room_id), type=request.type, summary_data=summary_data)
