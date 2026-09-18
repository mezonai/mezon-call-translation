from fastapi import APIRouter, Depends, HTTPException, Query

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.models.room_models import RoomIdPath
from orchestrator_service.models.transcript_models import TranscriptCorrectionRetryType
from orchestrator_service.services.transcript_correction_service import get_correction_service
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)
router = APIRouter(prefix="/transcript", tags=["Transcript"])


@router.post(
    "/{room_id}/retry",
    response_description="Run or retry LLM transcript correction on room messages",
)
async def correct_transcript(
    room_id: RoomIdPath,
    retry_type: TranscriptCorrectionRetryType = Query(
        TranscriptCorrectionRetryType.SECTION,
        description="Type of retry: 'section' (resume from last failure) or 'all' (restart from beginning)",
    ),

    auth: dict[str, str | bool] = Depends(verify_api_key),
):
    room_id_str = str(room_id)
    try:
        result = await get_correction_service().correct_transcript_for_room(room_id_str, retry_type)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    except Exception as e:
        logger.error(f"Transcript correction failed for room {room_id_str}: {e}")
        raise HTTPException(status_code=500, detail=str(e)) from e

    return {"status": "ok", "room_id": room_id_str, "corrected_messages_count": len(result)}
