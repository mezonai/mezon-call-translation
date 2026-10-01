import asyncio
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel, Field

from non_realtime_stt_service.service.parakeet.async_job_service import JobStatus, get_async_job_service
from non_realtime_stt_service.service.parakeet.parakeet_engine import get_parakeet_engine
from non_realtime_stt_service.utils.audio_converter import (
    cleanup_temp_files,
    convert_audio_to_wav,
    download_url_to_temp,
    get_audio_duration,
    save_bytes_to_temp,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/stt", tags=["Speech-to-Text Parakeet"])


# ============================================================================
# Pydantic Response Schemas (OpenAPI Documentation)
# ============================================================================

class SegmentModel(BaseModel):
    start: float = Field(..., description="Segment start timestamp in seconds")
    end: float = Field(..., description="Segment end timestamp in seconds")
    text: str = Field(..., description="Transcribed text for this segment")


class TranscriptionData(BaseModel):
    text: str = Field(..., description="Full combined transcription text")
    segments: List[SegmentModel] = Field(default_factory=list, description="List of timed segments")


class SyncTranscribeResponse(BaseModel):
    status: str = Field("success", description="Execution status")
    data: TranscriptionData = Field(..., description="Transcription results")
    batch_id: int = Field(..., description="Inference batch identifier")
    batch_size: int = Field(..., description="Number of items batched together in GPU")
    batch_proc_time: float = Field(..., description="GPU batch processing time in seconds")


class AsyncSubmitResponse(BaseModel):
    status: str = Field("ok", description="Submission status")
    job_id: str = Field(..., description="Unique job identifier to query status or result")
    state: str = Field(JobStatus.QUEUED.value, description="Initial job lifecycle state")
    created_at: float = Field(..., description="Job creation epoch timestamp")
    audio_duration_sec: float = Field(..., description="Normalized audio duration in seconds")


class JobStatusResponse(BaseModel):
    status: str = Field("ok", description="Query status")
    job_id: str = Field(..., description="Job identifier")
    state: str = Field(..., description="Current state: queued, inprogress, done, failed")
    created_at: Optional[float] = Field(None, description="Creation timestamp")
    updated_at: Optional[float] = Field(None, description="Last update timestamp")
    duration: float = Field(0.0, description="Audio duration in seconds")
    processing_time: float = Field(0.0, description="Inference processing time in seconds")
    error_message: Optional[str] = Field("", description="Error message if failed")


# ============================================================================
# Helper Functions
# ============================================================================

async def _extract_audio_source(
    request: Request,
    file: Optional[UploadFile] = None,
    url: Optional[str] = None,
) -> tuple[str, List[str], str]:
    """
    Parse audio input from either multipart file upload or remote URL.

    Returns:
        tuple of (normalized_wav_path, list_of_temp_files_to_clean, source_description)
    """
    raw_path: Optional[str] = None
    target_url: Optional[str] = url

    # Fallback: check for JSON body if neither file nor form 'url' were supplied
    content_type = request.headers.get("content-type", "")
    if not file and not target_url and "application/json" in content_type:
        try:
            body = await request.json()
            target_url = body.get("url")
        except Exception:
            pass

    if file and file.filename:
        content = await file.read()
        raw_path = save_bytes_to_temp(content, file.filename)
        source_desc = f"upload:{file.filename}"
    elif target_url:
        raw_path = await download_url_to_temp(target_url)
        source_desc = f"url:{target_url}"
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either an audio file upload or a 'url' parameter must be provided.",
        )

    # Convert audio to standard 16kHz mono WAV via FFmpeg
    try:
        wav_path = await asyncio.to_thread(convert_audio_to_wav, raw_path)
    except Exception as e:
        cleanup_temp_files(raw_path)
        logger.error("Audio normalization failed for source %s: %s", source_desc, e)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Audio normalization failed: {str(e)}",
        )

    return wav_path, [raw_path, wav_path], source_desc


# ============================================================================
# API Endpoints
# ============================================================================

@router.post(
    "/transcribe",
    response_model=SyncTranscribeResponse,
    summary="Transcribe audio synchronously",
)
async def transcribe_sync(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """
    Synchronous speech-to-text transcription:
    - Normalizes audio to 16kHz mono WAV.
    - Dynamically batches with concurrent requests into safe GPU VRAM budget.
    - Returns transcribed text and segment-level timestamps immediately upon completion.
    """
    engine = get_parakeet_engine()
    if not engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Parakeet ASR Engine is not initialized or still starting up.",
        )

    engine.in_flight_converting += 1
    temp_paths: List[str] = []

    try:
        wav_path, temp_paths, _ = await _extract_audio_source(request, file, url)
        duration = get_audio_duration(wav_path)
    except HTTPException:
        raise
    except Exception as e:
        cleanup_temp_files(*temp_paths)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio input: {str(e)}",
        )
    finally:
        engine.in_flight_converting -= 1

    event = asyncio.Event()
    item = {
        "job_id": str(uuid.uuid4()),
        "file_path": wav_path,
        "temp_paths": temp_paths,
        "duration": duration,
        "is_async": False,
        "event": event,
        "result": None,
    }

    await engine.request_queue.put(item)
    await event.wait()

    res = item.get("result") or {}
    if res.get("status") == "error":
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=res.get("message", "Inference error occurred during batch processing."),
        )

    return res


@router.post(
    "/transcribe/async",
    response_model=AsyncSubmitResponse,
    summary="Submit audio transcription job asynchronously",
)
async def transcribe_async(
    request: Request,
    file: Optional[UploadFile] = File(None),
    url: Optional[str] = Form(None),
):
    """
    Asynchronous speech-to-text transcription:
    - Enqueues task and returns a unique `job_id` with initial state 'queued'.
    - Audio is transcribed in the background with dynamic batching.
    - Client can periodically query status or fetch results once finished.
    """
    engine = get_parakeet_engine()
    if not engine.is_initialized:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Parakeet ASR Engine is not initialized or still starting up.",
        )

    engine.in_flight_converting += 1
    temp_paths: List[str] = []

    try:
        wav_path, temp_paths, source_desc = await _extract_audio_source(request, file, url)
        duration = get_audio_duration(wav_path)
    except HTTPException:
        raise
    except Exception as e:
        cleanup_temp_files(*temp_paths)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid audio input: {str(e)}",
        )
    finally:
        engine.in_flight_converting -= 1

    job_id = str(uuid.uuid4())
    job_service = get_async_job_service()
    job_record = await job_service.create_job(job_id, audio_source=source_desc)

    item = {
        "job_id": job_id,
        "file_path": wav_path,
        "temp_paths": temp_paths,
        "duration": duration,
        "is_async": True,
        "event": None,
        "result": None,
    }

    await engine.request_queue.put(item)

    return {
        "status": "ok",
        "job_id": job_id,
        "state": job_record["status"],
        "created_at": job_record["created_at"],
        "audio_duration_sec": round(duration, 2),
    }


@router.get(
    "/jobs/{job_id}/status",
    response_model=JobStatusResponse,
    summary="Check asynchronous job status",
)
async def get_job_status(job_id: str):
    """
    Query the lifecycle state of a submitted job.
    Possible states: `queued`, `inprogress`, `done`, `failed`.
    """
    job_service = get_async_job_service()
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"STT Job '{job_id}' not found or expired.",
        )

    return {
        "status": "ok",
        "job_id": job["job_id"],
        "state": job["status"],
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "duration": job.get("duration", 0.0),
        "processing_time": job.get("processing_time", 0.0),
        "error_message": job.get("error_message", ""),
    }


@router.get(
    "/jobs/{job_id}/result",
    summary="Retrieve results for completed job",
    responses={
        200: {"description": "Job completed successfully"},
        202: {"description": "Job is still pending or processing"},
        404: {"description": "Job ID not found"},
        500: {"description": "Job failed during processing"},
    },
)
async def get_job_result(job_id: str):
    """
    Fetch the transcript and segment timestamps for a completed job:
    - Returns **HTTP 202 Accepted** if the job is still `queued` or `inprogress`.
    - Returns **HTTP 200 OK** with complete transcript and segment timestamps once `done`.
    - Returns **HTTP 500** if the job encountered a processing error.
    """
    job_service = get_async_job_service()
    job = await job_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"STT Job '{job_id}' not found or expired.",
        )

    current_state = job.get("status")

    # Pending or running: return HTTP 202
    if current_state in (JobStatus.QUEUED.value, JobStatus.INPROGRESS.value):
        return Response(
            content=f'{{"status":"pending","job_id":"{job_id}","state":"{current_state}"}}',
            status_code=status.HTTP_202_ACCEPTED,
            media_type="application/json",
        )

    # Failed
    if current_state == JobStatus.FAILED.value:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=job.get("error_message", "Transcription processing failed."),
        )

    # Completed
    return {
        "status": "success",
        "job_id": job_id,
        "data": job.get("result"),
        "duration": job.get("duration", 0.0),
        "processing_time": job.get("processing_time", 0.0),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }
