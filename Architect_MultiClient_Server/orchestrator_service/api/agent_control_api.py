"""
Agent Control API - Control transcription via LiveKit data channel
"""
import json
import time
from typing import Literal, Optional, Dict, Any
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from livekit import api
from livekit.protocol.models import DataPacket

from orchestrator_service.utils.logger import get_logger
from orchestrator_service.services.livekit_client import get_livekit_service
from orchestrator_service.auth.transcript_auth import verify_api_key

router = APIRouter(prefix="/api/agent-control", tags=["Agent Control"])
logger = get_logger(__name__)


class TranscriptControlRequest(BaseModel):
    """Request model for transcript control"""
    room_name: str = Field(..., description="Room name to control transcript")
    action: Literal["enable", "disable"] = Field(
        ..., 
        description="Action to perform: enable to start transcription, disable to stop transcription"
    )
    
    class Config:
        json_schema_extra = {
            "example": {
                "room_name": "my-room-123",
                "action": "enable"
            }
        }


class TranscriptControlResponse(BaseModel):
    """Response model for transcript control"""
    status: str = Field(..., description="Status of the operation")
    message: str = Field(..., description="Human-readable message")
    room_name: str = Field(..., description="Room name")
    action: str = Field(..., description="Action performed")
    agent_response: Optional[Dict[str, Any]] = Field(None, description="Response from agent if available")


@router.post("/transcript", response_model=TranscriptControlResponse)
async def control_transcript(
    request: TranscriptControlRequest,
    auth: Dict[str, Any] = Depends(verify_api_key)
):
    """
    Control transcription for a specific room via LiveKit data channel.
    
    Sends a control message to the agent in the room to enable/disable transcription.
    The agent must be running and listening on the "agent_control" data channel topic.
    
    **Actions:**
    - `enable`: Enable transcription for all participants
    - `disable`: Disable all active transcriptions
    
    **Requirements:**
    - Room must exist and be active
    - Agent must be present in the room
    - Agent must support agent_control protocol
    
    **Example:**
    ```json
    {
        "room_name": "my-room-123",
        "action": "enable"
    }
    ```
    """
    try:
        livekit_service = get_livekit_service()
        
        if not livekit_service.is_available:
            raise HTTPException(
                status_code=503,
                detail="LiveKit API not available. Please check server configuration."
            )
        
        client = livekit_service.get_client()
        
        # Prepare control message for agent
        control_message = {
            "type": "agent_control",
            "action": request.action,
            "timestamp": int(time.time() * 1000)
        }
        
        message_bytes = json.dumps(control_message).encode("utf-8")
        
        # Send data to room via LiveKit API
        # The agent will receive this on the "agent_control" topic
        try:
            # Send data to the room - all participants will receive it
            # The agent listening on "agent_control" topic will handle it
            await client.room.send_data(
                api.SendDataRequest(
                    room=request.room_name,
                    data=message_bytes,
                    kind=DataPacket.Kind.RELIABLE,
                    topic="agent_control"
                )
            )
            
            logger.info(f"Sent transcript control to room {request.room_name}: action={request.action}")
            
            return TranscriptControlResponse(
                status="ok",
                message=f"Transcript control message sent successfully to room '{request.room_name}'",
                room_name=request.room_name,
                action=request.action,
                agent_response={
                    "note": "Agent will process the request asynchronously. Check agent logs for confirmation."
                }
            )
            
        except Exception as e:
            logger.error(f"Failed to send data to room {request.room_name}: {e}")
            
            # Check if it's a room not found error
            if "not found" in str(e).lower():
                raise HTTPException(
                    status_code=404,
                    detail=f"Room '{request.room_name}' not found or not active"
                )
            
            raise HTTPException(
                status_code=500,
                detail=f"Failed to send control message: {str(e)}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in transcript control: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error: {str(e)}"
        )


@router.get("/health", response_description="Check agent control API health")
async def health_check():
    """
    Check if the agent control API is properly configured and ready.
    
    Returns configuration status and LiveKit connectivity.
    """
    try:
        livekit_service = get_livekit_service()
        health_status = await livekit_service.health_check()
        
        return {
            "status": "ok" if health_status["status"] == "ok" else "degraded",
            "message": "Agent control API is operational",
            "livekit": health_status,
            "capabilities": [
                "enable",
                "disable"
            ]
        }
    except Exception as e:
        logger.error(f"Health check failed: {e}")
        return {
            "status": "error",
            "message": str(e),
            "livekit": {"status": "error", "message": str(e)}
        }
