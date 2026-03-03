"""
Internal API endpoints for room summary
"""
import asyncio
from fastapi import APIRouter, HTTPException, Body, Depends, Header, Query
from pydantic import BaseModel
from orchestrator_service.services.summary_service import get_summary_service
from orchestrator_service.config.application_config import get_config
from datetime import datetime
from typing import Optional
from orchestrator_service.services.mongodb_service import get_mongodb_service
from orchestrator_service.api.sse_metadata_api import metadata_channel

internal_router = APIRouter(prefix="/api/internal", tags=["Internal"])
client_router = APIRouter(prefix="/api/summary", tags=["Summary"])

class SummaryRequest(BaseModel):
    room_id: str

async def verify_internal_api_key(x_api_key: str = Header(..., alias="x-internal-api-key")):
    """Verify internal API key from header"""
    config = get_config()
    expected_key = config.server.internal_api_key
    
    if not expected_key:
        raise HTTPException(status_code=500, detail="Internal API key not configured on server")
        
    if x_api_key != expected_key:
        raise HTTPException(status_code=403, detail="Invalid internal API key")
    return x_api_key

@internal_router.post("/summary", response_description="Generate room summary", dependencies=[Depends(verify_internal_api_key)])
async def generate_room_summary(request: SummaryRequest = Body(...)):
    """
    Internal endpoint to generate a summary for a room.
    Input: {"room_id": "..."}
    
    If the room is associated with an interview, also sends track data to interview webhook.
    """
    
    mongodb = get_mongodb_service()
    room = await mongodb.get_room_by_id(request.room_id)
    room_name = room.get("room_name") if room else "unknown"
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")

    await metadata_channel.push_room_record_done(
        room_id=request.room_id,
        room_name=room_name
    )

    service = get_summary_service()
    result = await service.generate_summary(request.room_id)


    if not result:
        raise HTTPException(status_code=404, detail="Room not found or no transcripts available")
    
    await metadata_channel.push_room_summary_done(
        room_id=request.room_id,
        room_name=room_name
    )

    # Convert ObjectId to str for JSON serialization
    if "_id" in result:
        result["_id"] = str(result["_id"])
        
    return {
        "status": "ok",
        "data": result
    }

@client_router.get("/room/{room_name}", response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: str,
    start_time: Optional[datetime] = Query(None, description="Start time for room summary (ISO format: 2024-01-01T00:00:00)"),
    end_time: Optional[datetime] = Query(None, description="End time for room summary (ISO format: 2024-01-31T23:59:59)"),
    ):
    """
    Get summary by room name.
    """
    mongodb = get_mongodb_service()
    summaries = await mongodb.get_summary_by_room_name(room_name, start_time, end_time)
    return {
        "status": "ok",
        "data": summaries,
        "count": len(summaries)
    }

@client_router.get("/room/id/{room_id}", response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: str,
    ):
    """
    Get summary by room id.
    """
    mongodb = get_mongodb_service()
    summaries = await mongodb.get_summary_by_room_id(room_id)
    return {
        "status": "ok",
        "data": summaries,
        "count": len(summaries)
    }
