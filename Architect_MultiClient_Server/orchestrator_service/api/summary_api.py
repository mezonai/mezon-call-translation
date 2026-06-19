"""
Internal API endpoints for room summary
"""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from orchestrator_service.models.summary_models import RetryType, RoomSummaryResponse
from orchestrator_service.services.postgresql.pg_transcript_repository import (
    PgTranscriptRepository,
)
from orchestrator_service.services.summary_service import get_summary_service

client_router = APIRouter(prefix="/api/summary", tags=["Summary"])


@client_router.get("/room/{room_name}", response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: str,
    start_time: datetime | None = Query(
        None,
        description="Start time for room summary (ISO format: 2024-01-01T00:00:00)",
    ),
    end_time: datetime | None = Query(None, description="End time for room summary (ISO format: 2024-01-31T23:59:59)"),
):
    """
    Get summary by room name.
    """
    pg_repo = PgTranscriptRepository()
    summaries = await pg_repo.get_summary_by_room_name(room_name, start_time, end_time)

    summary_models = []
    for summary in summaries:
        if summary.get("room_id") is not None:
            summary["room_id"] = str(summary["room_id"])
        summary_models.append(RoomSummaryResponse.model_construct(**summary))

    return {"status": "ok", "data": summary_models, "count": len(summary_models)}


@client_router.get("/room/id/{room_id}", response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: str,
):
    """
    Get summary by room id.
    """
    pg_repo = PgTranscriptRepository()

    summary = await pg_repo.get_summary_by_room_id(room_id)

    if summary.get("room_id") is not None:
        summary["room_id"] = str(summary["room_id"])
    summary = RoomSummaryResponse.model_construct(**summary)

    return {"status": "ok", "data": summary}


@client_router.post(
    "/retry/{room_id}",
    response_description="Re-run LLM summary using existing full_text",
)
async def retry_summary(
    room_id: str,
    type: RetryType = Query(RetryType.ALL, description="Type of retry: 'summary', 'action_items', or 'all'"),
):
    try:
        summary_data = await get_summary_service().retry_summary_from_full_text(room_id, retry_type=type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    if summary_data is None:
        raise HTTPException(status_code=500, detail="Update summary_data to DB failed")

    return {
        "status": "ok",
        "room_id": room_id,
        "type": type.value,
        "summary_data": summary_data,
    }
