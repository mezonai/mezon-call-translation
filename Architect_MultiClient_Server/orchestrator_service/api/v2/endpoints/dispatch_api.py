from google.protobuf.json_format import MessageToDict
from enum import Enum
from typing import Dict, Any, Optional

from orchestrator_service.auth.authorization import AuthContext, require_any_permission
try:
    from livekit import api
    from livekit.api import twirp_client
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel, Field
from orchestrator_service.services.livekit_client import get_livekit_service

router = APIRouter()


class DispatchStatus(str, Enum):
    """Dispatch operation status codes."""
    ERROR = "error"
    EXISTS = "exists"
    CREATED = "created"
    NOT_FOUND = "not_found"
    CANCELLED = "cancelled"


class LiveKitError(Exception):
    """Custom exception for LiveKit operations."""
    pass


def get_livekit_client():
    """
    Get LiveKit client and agent name from centralized service.
    
    Returns:
        Tuple of (LiveKitAPI, agent_name)
    """
    service = get_livekit_service()
    if not service.is_available:
        raise LiveKitError("LiveKit API not available. Please install livekit-api package.")
    
    return service.get_client(), service.get_agent_name()


async def list_dispatches(room_name: str):
    """List all dispatches for a room."""
    client, _ = get_livekit_client()
    try:
        return await client.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        raise LiveKitError(f"LiveKit server error: {e}")


async def find_agent_dispatch(dispatches, agent_name: str) -> Optional[Any]:
    """Find dispatch by agent name."""
    for dispatch in dispatches:
        if dispatch.agent_name == agent_name:
            return dispatch
    return None

class DispatchRequestModel(BaseModel):
    room_name: str = Field(..., description="Room name")
    class Config:
        json_schema_extra = {
            "example": {
                "room_name": "Interview Room 1"
            }
        }

async def ensure_dispatch(room_name: str) -> Dict[str, Any]:
    """
    Ensure a dispatch exists for the given room.
    Creates one if it doesn't exist.
    
    Returns:
        Dict with status and relevant data
    """
    try:
        client, agent_name = get_livekit_client()
        
        # Check existing dispatches
        dispatches = await client.agent_dispatch.list_dispatch(room_name=room_name)
        
        # Check if dispatch already exists
        if await find_agent_dispatch(dispatches, agent_name):
            return {
                "status": DispatchStatus.EXISTS,
                "message": "Dispatch already exists"
            }
        
        # Create new dispatch
        dispatch = await client.agent_dispatch.create_dispatch(
            api.CreateAgentDispatchRequest(
                agent_name=agent_name,
                room=room_name
            )
        )
        
        return {
            "status": DispatchStatus.CREATED,
            "dispatch": MessageToDict(dispatch, preserving_proto_field_name=True)
        }
        
    except LiveKitError as e:
        return {"status": DispatchStatus.ERROR, "message": str(e)}
    except twirp_client.TwirpError as e:
        return {"status": DispatchStatus.ERROR, "message": f"LiveKit server error: {e}"}


async def cancel_dispatch(room_name: str) -> Dict[str, Any]:
    """
    Cancel an existing dispatch for the given room.
    
    Returns:
        Dict with status and relevant data
    """
    try:
        client, agent_name = get_livekit_client()
        
        # Get dispatches
        dispatches = await client.agent_dispatch.list_dispatch(room_name=room_name)
        
        # Find target dispatch
        target_dispatch = await find_agent_dispatch(dispatches, agent_name)
        
        if not target_dispatch:
            return {
                "status": DispatchStatus.NOT_FOUND,
                "message": f"No active dispatch found for agent '{agent_name}'"
            }
        
        # Delete dispatch
        await client.agent_dispatch.delete_dispatch(
            target_dispatch.id,
            target_dispatch.room,
        )
        
        return {
            "status": DispatchStatus.CANCELLED,
            "message": f"Dispatch for agent '{target_dispatch.agent_name}' has been cancelled.",
            "dispatch": MessageToDict(target_dispatch, preserving_proto_field_name=True),
        }
        
    except LiveKitError as e:
        return {"status": DispatchStatus.ERROR, "message": str(e)}
    except twirp_client.TwirpError as e:
        return {"status": DispatchStatus.ERROR, "message": f"Failed to cancel dispatch: {e}"}

@router.post("/create_dispatch")
async def api_create_dispatch(body: DispatchRequestModel, auth: AuthContext = Depends(require_any_permission("agent:control"))) -> Dict[str, Any]:
    """Create a dispatch for the specified room."""
    
    result = await ensure_dispatch(body.room_name)
    if result["status"] == DispatchStatus.ERROR:
        raise HTTPException(status_code=500, detail=result["message"])
    
    return result

@router.post("/cancel_dispatch")
async def api_cancel_dispatch(body: DispatchRequestModel, auth: AuthContext = Depends(require_any_permission("agent:control"))) -> Dict[str, Any]:
    """Cancel a dispatch for the specified room."""
    
    result = await cancel_dispatch(body.room_name)
    if result["status"] == DispatchStatus.ERROR:
        raise HTTPException(status_code=500, detail=result["message"])
    
    return result