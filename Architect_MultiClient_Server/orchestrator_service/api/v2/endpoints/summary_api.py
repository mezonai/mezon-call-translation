"""
Internal API endpoints for room summary
"""
from fastapi import APIRouter, Query, Depends
from datetime import datetime
from typing import Optional, Dict, Any
from orchestrator_service.services.mongodb.mongodb_service import MongoDBService
from orchestrator_service.auth.jwt_auth import verify_jwt

client_router = APIRouter(prefix="/api/summary", tags=["Summary"])

@client_router.get("/room/{room_name}", response_description="Get summary by room ID")
async def get_summary_by_room_name(
    room_name: str,
    start_time: Optional[datetime] = Query(None, description="Start time for room summary (ISO format: 2024-01-01T00:00:00)"),
    end_time: Optional[datetime] = Query(None, description="End time for room summary (ISO format: 2024-01-31T23:59:59)"),
    user: Dict[str, Any] = Depends(verify_jwt)
):
    """
    Get summary by room name.
    """
    mongodb = MongoDBService()
    summaries = await mongodb.get_summary_by_room_name(room_name, start_time, end_time)
    return {
        "status": "ok",
        "data": summaries,
        "count": len(summaries)
    }

@client_router.get("/room/id/{room_id}", response_description="Get summary by room ID")
async def get_summary_by_room_id(
    room_id: str,
    user: Dict[str, Any] = Depends(verify_jwt)
):
    """
    Get summary by room id.
    """
    mongodb = MongoDBService()
    summary = await mongodb.get_summary_by_room_id(room_id)
    return {
        "status": "ok",
        "data": summary
    }
