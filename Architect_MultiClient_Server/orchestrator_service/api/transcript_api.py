"""
Transcript API endpoints for querying transcript chunks and segments from MongoDB
- Get transcript chunks by track
- Search transcripts
- Filter by confidence
- Health check
"""

from datetime import datetime
from typing import Dict, Any
from fastapi import APIRouter, HTTPException, Query, Depends

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.mongodb_service import get_mongodb_service
from orchestrator_service.auth.transcript_auth import verify_api_key
from orchestrator_service.config.transcript_config import VALIDATION_CONFIG as VC
from orchestrator_service.utils.transcript_validators import (
    TrackIdPath,
    ChunkIndexPath,
    SearchQuery,
    LimitQuery,
    SkipQuery,
    validate_time_range,
    validate_confidence_range
)

router = APIRouter(prefix="/api/transcripts", tags=["Transcripts"])
logger = get_logger(__name__)


# ========================================
# 📝 TRANSCRIPT CHUNKS ENDPOINTS
# ========================================

@router.get("/tracks/{track_id}/chunks", response_description="Get all chunks for a track")
async def get_chunks_by_track(
    track_id: TrackIdPath,
    sorted_by_index: bool = Query(True, description="Sort chunks by index"),
    limit: LimitQuery = VC.LIMIT_TRANSCRIPT_CHUNKS,
    skip: SkipQuery = VC.DEFAULT_SKIP,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get transcript chunks for a track with pagination.
    
    - **track_id**: The MongoDB ObjectId of the track
    - **sorted_by_index**: Whether to sort chunks by index (default: True)
    - **limit**: Maximum number of chunks to return
    - **skip**: Number of records to skip for pagination
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        chunks = await mongodb.get_chunks_by_track(
            track_id, 
            sorted_by_index=sorted_by_index,
            limit=limit,
            skip=skip
        )
        count = await mongodb.count_chunks_by_track(track_id)
        
        for chunk in chunks:
            chunk["_id"] = str(chunk["_id"])
            if "track_ref_id" in chunk:
                chunk["track_ref_id"] = str(chunk["track_ref_id"])
        
        return {
            "status": "ok",
            "track_id": track_id,
            "total_chunks": count,
            "limit": limit,
            "skip": skip,
            "chunks": chunks
        }
    except Exception as e:
        logger.error(f"Failed to get chunks by track: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tracks/{track_id}/chunks/{chunk_index}", response_description="Get chunk by index")
async def get_chunk_by_index(
    track_id: TrackIdPath,
    chunk_index: ChunkIndexPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get a specific chunk by track ID and chunk index.
    
    - **track_id**: The MongoDB ObjectId of the track
    - **chunk_index**: The index of the chunk to retrieve
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        chunk = await mongodb.get_chunk_by_index(track_id, chunk_index)
        if not chunk:
            raise HTTPException(
                status_code=404, 
                detail=f"Chunk with index {chunk_index} not found for track '{track_id}'"
            )
        
        chunk["_id"] = str(chunk["_id"])
        if "track_ref_id" in chunk:
            chunk["track_ref_id"] = str(chunk["track_ref_id"])
        
        return {
            "status": "ok",
            "chunk": chunk
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get chunk: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tracks/{track_id}/chunks/time-range/query", response_description="Get chunks by time range")
async def get_chunks_by_time_range(
    track_id: TrackIdPath,
    start_time: float = Query(..., ge=VC.MIN_TIME_SECONDS, le=VC.MAX_TIME_SECONDS, description=f"Start time in seconds ({VC.MIN_TIME_SECONDS}-{VC.MAX_TIME_SECONDS})"),
    end_time: float = Query(..., ge=VC.MIN_TIME_SECONDS, le=VC.MAX_TIME_SECONDS, description=f"End time in seconds ({VC.MIN_TIME_SECONDS}-{VC.MAX_TIME_SECONDS})"),
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get transcript chunks within a time range.
    
    - **track_id**: The MongoDB ObjectId of the track
    - **start_time**: Start time in seconds (max 24 hours)
    - **end_time**: End time in seconds (max 24 hours)
    """
    # Validate start_time < end_time
    validate_time_range(start_time, end_time)
    
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        chunks = await mongodb.get_chunks_by_time_range(track_id, start_time, end_time)
        
        for chunk in chunks:
            chunk["_id"] = str(chunk["_id"])
            if "track_ref_id" in chunk:
                chunk["track_ref_id"] = str(chunk["track_ref_id"])
        
        return {
            "status": "ok",
            "track_id": track_id,
            "time_range": {
                "start": start_time,
                "end": end_time
            },
            "chunks": chunks
        }
    except Exception as e:
        logger.error(f"Failed to get chunks by time range: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tracks/{track_id}/transcript", response_description="Get full transcript")
async def get_full_transcript(
    track_id: TrackIdPath,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get the full transcript for a track by combining all chunks.
    
    - **track_id**: The MongoDB ObjectId of the track
    
    Returns all transcript segments in order.
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        segments = await mongodb.get_full_transcript(track_id)
        
        return {
            "status": "ok",
            "track_id": track_id,
            "total_segments": len(segments),
            "transcript": segments
        }
    except Exception as e:
        logger.error(f"Failed to get full transcript: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tracks/{track_id}/search", response_description="Search transcript")
async def search_transcript(
    track_id: TrackIdPath,
    q: SearchQuery,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Search for text in transcript segments.
    
    - **track_id**: The MongoDB ObjectId of the track
    - **q**: Text to search for (case-insensitive)
    """
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        results = await mongodb.search_transcript_text(track_id, q)
        
        return {
            "status": "ok",
            "track_id": track_id,
            "query": q,
            "total_matches": len(results),
            "results": results
        }
    except Exception as e:
        logger.error(f"Failed to search transcript: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/tracks/{track_id}/segments/confidence", response_description="Filter by confidence")
async def get_segments_by_confidence(
    track_id: TrackIdPath,
    min_confidence: float = Query(VC.MIN_CONFIDENCE, ge=VC.MIN_CONFIDENCE, le=VC.MAX_CONFIDENCE, description=f"Minimum confidence ({VC.MIN_CONFIDENCE}-{VC.MAX_CONFIDENCE})"),
    max_confidence: float = Query(VC.MAX_CONFIDENCE, ge=VC.MIN_CONFIDENCE, le=VC.MAX_CONFIDENCE, description=f"Maximum confidence ({VC.MIN_CONFIDENCE}-{VC.MAX_CONFIDENCE})"),
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Get transcript segments filtered by confidence range.
    
    - **track_id**: The MongoDB ObjectId of the track
    - **min_confidence**: Minimum confidence threshold (0.0 - 1.0)
    - **max_confidence**: Maximum confidence threshold (0.0 - 1.0)
    """
    # Validate min_confidence <= max_confidence
    validate_confidence_range(min_confidence, max_confidence)
    
    try:
        mongodb = get_mongodb_service()
        if not mongodb.connected:
            await mongodb.connect()
        
        segments = await mongodb.get_segments_by_confidence(
            track_id, 
            min_confidence=min_confidence,
            max_confidence=max_confidence
        )
        
        return {
            "status": "ok",
            "track_id": track_id,
            "confidence_range": {
                "min": min_confidence,
                "max": max_confidence
            },
            "total_segments": len(segments),
            "segments": segments
        }
    except Exception as e:
        logger.error(f"Failed to get segments by confidence: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ========================================
# 🔧 UTILITY ENDPOINTS
# ========================================

@router.get("/health")
async def health_check():
    """
    Health check endpoint for transcript API.
    
    Returns MongoDB connection status.
    """
    try:
        mongodb = get_mongodb_service()
        connected = mongodb.connected
        
        if not connected:
            connected = await mongodb.connect()
        
        return {
            "status": "ok" if connected else "degraded",
            "service": "transcript-api",
            "mongodb_connected": connected,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "error",
            "service": "transcript-api",
            "mongodb_connected": False,
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }
