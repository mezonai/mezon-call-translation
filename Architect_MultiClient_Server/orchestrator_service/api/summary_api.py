"""
Query API endpoints for room summary
"""
from fastapi import APIRouter, HTTPException, Query
from datetime import datetime
from typing import Optional
from orchestrator_service.services.mongodb_service import get_mongodb_service

client_router = APIRouter(prefix="/api/summary", tags=["Summary"])

@client_router.get("/user/{user_id}", response_description="Get summaries by user ID")
async def get_user_summaries(user_id: str):
    """
    Get all summaries where the user participated.
    """
    mongodb = get_mongodb_service()
    summaries = await mongodb.get_summaries_by_participant(user_id)
    
    # Convert ObjectId to str
    for summary in summaries:
        if "_id" in summary:
            summary["_id"] = str(summary["_id"])
            
    return {
        "status": "ok",
        "count": len(summaries),
        "data": summaries
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
    # remove unnecessary fields
    for summary in summaries:
        summary.pop("_id", None)
        summary.pop("summary_text", None)
    return {
        "status": "ok",
        "data": summaries,
        "count": len(summaries)
    }
