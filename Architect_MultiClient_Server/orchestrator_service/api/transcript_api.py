from fastapi import APIRouter, HTTPException, Query

from orchestrator_service.models.transcript_models import TranscriptCorrectionRetryType
from orchestrator_service.services.postgresql.pg_outbox_repository import get_pg_outbox_repository
from orchestrator_service.services.transcript_correction_service import get_correction_service

router = APIRouter(prefix="/api/transcript", tags=["Transcript"])


@router.post(
    "/{room_id}/retry",
    response_description="Run or retry LLM transcript correction on room messages",
)
async def correct_transcript(
    room_id: str,
    retry_type: TranscriptCorrectionRetryType = Query(
        TranscriptCorrectionRetryType.SECTION,
        description="Type of retry: 'section' (resume from last failure) or 'all' (restart from beginning)",
    ),
):
    try:
        result = await get_correction_service().correct_transcript_for_room(room_id, retry_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        # On failure -> create outbox task for retry
        outbox_repo = get_pg_outbox_repository()
        await outbox_repo.add_retry_transcript_correction_task_to_outbox(
            room_id=room_id,
            retry_type=retry_type.value,
            error_msg=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "ok", "room_id": room_id, "corrected_messages_count": len(result)}
