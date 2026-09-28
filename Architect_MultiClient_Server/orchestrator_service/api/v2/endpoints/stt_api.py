import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.services.non_realtime_stt_client import get_non_realtime_stt_client
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/stt", tags=["Speech-to-Text Gateway"])


# ============================================================================
# Pydantic Response Schemas (OpenAPI Documentation)
# ============================================================================

class SegmentSchema(BaseModel):
    start: float = Field(..., description="Start timestamp in seconds")
    end: float = Field(..., description="End timestamp in seconds")
    text: str = Field(..., description="Transcribed text for this segment")


class TranscriptionDataSchema(BaseModel):
    text: str = Field(..., description="Full combined transcription text")
    segments: List[SegmentSchema] = Field(default_factory=list, description="List of timed segments")


class SyncTranscribeResponseSchema(BaseModel):
    status: str = Field("success", description="Status string")
    data: TranscriptionDataSchema = Field(..., description="Transcription payload")
    batch_id: int = Field(..., description="Inference batch identifier")
    batch_size: int = Field(..., description="Batch size processed by GPU")
    batch_proc_time: float = Field(..., description="Batch GPU processing time in seconds")


class AsyncSubmitResponseSchema(BaseModel):
    status: str = Field("ok", description="Status string")
    job_id: str = Field(..., description="Job UUID for querying status/result")
    state: str = Field("queued", description="Initial lifecycle state")
    created_at: float = Field(..., description="Job creation timestamp")
    audio_duration_sec: float = Field(..., description="Estimated audio duration in seconds")


class JobStatusResponseSchema(BaseModel):
    status: str = Field("ok", description="Query status")
    job_id: str = Field(..., description="Job identifier")
    state: str = Field(..., description="Current state: queued, inprogress, done, failed")
    created_at: Optional[float] = Field(None, description="Creation timestamp")
    updated_at: Optional[float] = Field(None, description="Last update timestamp")
    duration: float = Field(0.0, description="Audio duration in seconds")
    processing_time: float = Field(0.0, description="GPU processing time in seconds")
    error_message: Optional[str] = Field("", description="Error message if failed")


# ============================================================================
# Helpers
# ============================================================================

async def _parse_audio_params(
    request: Request,
    file: Optional[UploadFile] = None,
    url: Optional[str] = None,
) -> tuple[Optional[bytes], Optional[str], Optional[str]]:
    """
    Extract file bytes / filename or URL parameter from incoming request.
    Supports both multipart/form-data and application/json payloads.
    """
    target_url = url
    content_type = request.headers.get("content-type", "")

    # Check for JSON body if neither file nor form 'url' were supplied
    if not file and not target_url and "application/json" in content_type:
        try:
            body = await request.json()
            target_url = body.get("url")
        except Exception:
            pass

    file_bytes: Optional[bytes] = None
    filename: Optional[str] = None

    if file and file.filename:
        file_bytes = await file.read()
        filename = file.filename

    if not file_bytes and not target_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either an audio file upload or a 'url' parameter must be provided.",
        )

    return file_bytes, filename, target_url


# ============================================================================
# Gateway Endpoints
# ============================================================================

@router.post(
    "/transcribe",
    response_model=SyncTranscribeResponseSchema,
    summary="Synchronous speech transcription through gateway",
)
async def transcribe_sync(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    auth: dict[str, str | bool] = Depends(verify_api_key),
):
    """
    Synchronous STT gateway endpoint:
    - Authenticates client credentials via API secret / token.
    - Forwards audio bytes or URL to Non-Realtime STT Service.
    - Waits for GPU dynamic batch inference and returns text with segment timestamps.
    """
    file_bytes, filename, target_url = await _parse_audio_params(request, file, url)
    client = get_non_realtime_stt_client()
    return await client.transcribe_sync(
        file_bytes=file_bytes,
        filename=filename,
        url=target_url,
    )


@router.post(
    "/transcribe/async",
    response_model=AsyncSubmitResponseSchema,
    summary="Submit asynchronous speech transcription job",
)
async def transcribe_async(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
    auth: dict[str, str | bool] = Depends(verify_api_key),
):
    """
    Asynchronous STT gateway endpoint:
    - Authenticates client credentials via API secret / token.
    - Enqueues task and returns job_id immediately with 'queued' status.
    - Client can periodically poll status or retrieve results when done.
    """
    file_bytes, filename, target_url = await _parse_audio_params(request, file, url)
    client = get_non_realtime_stt_client()
    return await client.transcribe_async(
        file_bytes=file_bytes,
        filename=filename,
        url=target_url,
    )


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusResponseSchema,
    summary="Check asynchronous transcription job status",
)
async def get_job_status(
    job_id: str,
    auth: dict[str, str | bool] = Depends(verify_api_key),
):
    """
    Check current lifecycle state of an asynchronous STT job:
    Returns 'queued', 'inprogress', 'done', or 'failed'.
    """
    client = get_non_realtime_stt_client()
    return await client.get_job_status(job_id)


@router.get(
    "/jobs/{job_id}/result",
    summary="Retrieve results for completed transcription job",
    responses={
        200: {"description": "Job completed successfully"},
        202: {"description": "Job is still pending or processing"},
        404: {"description": "Job ID not found"},
        500: {"description": "Job failed during processing"},
    },
)
async def get_job_result(
    job_id: str,
    response: Response,
    auth: dict[str, str | bool] = Depends(verify_api_key),
):
    """
    Retrieve results of an asynchronous STT job:
    - Returns **HTTP 202 Accepted** if still `queued` or `inprogress`.
    - Returns **HTTP 200 OK** with complete transcript and segment timestamps when `done`.
    - Returns **HTTP 500** if the job failed.
    """
    client = get_non_realtime_stt_client()
    status_code, data = await client.get_job_result(job_id)
    response.status_code = status_code
    return data
