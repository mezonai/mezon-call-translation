from google.protobuf.json_format import MessageToDict
try:
    from livekit import api
    from livekit.api import twirp_client
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False
    
import os
from dotenv import load_dotenv
load_dotenv()

from fastapi import APIRouter, HTTPException
import httpx
from pydantic import BaseModel
from api.verify_account import authenticate_account

router = APIRouter()

class AccountModel(BaseModel):
    appid: str
    token: str

class DispatchRequestModel(BaseModel):
    account: AccountModel
    room_name: str

async def ensure_dispatch(room_name: str):

    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error", 
            "message": "LiveKit API not available. Please install livekit-api package."
        }  
    url = os.environ.get("LIVEKIT_HTTP_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    agent_name = os.environ.get("LIVEKIT_AGENT_NAME")

    lkapi = api.LiveKitAPI(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
    )

    try:
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"LiveKit server error: {e}"}

    if any(d.agent_name == agent_name for d in dispatches):
        await lkapi.aclose()
        return {"status": "exists", "message": "Dispatch already exists"}

    dispatch = await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=agent_name,
            room=room_name
        )
    )
    await lkapi.aclose()

    dispatch_dict = MessageToDict(dispatch, preserving_proto_field_name=True)

    return {"status": "created", "dispatch": dispatch_dict}


async def cancel_dispatch(room_name: str):

    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error",
            "message": "LiveKit API not available. Please install livekit-api package."
        }

    url = os.environ.get("LIVEKIT_HTTP_URL")
    api_key = os.environ.get("LIVEKIT_API_KEY")
    api_secret = os.environ.get("LIVEKIT_API_SECRET")
    agent_name = os.environ.get("LIVEKIT_AGENT_NAME")

    lkapi = api.LiveKitAPI(
        url=url,
        api_key=api_key,
        api_secret=api_secret,
    )

    try:
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"LiveKit server error: {e}"}


    target_dispatch = None
    for d in dispatches:
        if d.agent_name == agent_name:
            target_dispatch = d
            break

    if not target_dispatch:
        await lkapi.aclose()
        return {
            "status": "not_found",
            "message": f"No active dispatch found for agent '{agent_name}'"
        }

    try:
        await lkapi.agent_dispatch.delete_dispatch(
            target_dispatch.id,
            target_dispatch.room,
        )
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"Failed to cancel dispatch: {e}"}

    await lkapi.aclose()

    return {
        "status": "cancelled",
        "message": f"Dispatch for agent '{target_dispatch.agent_name}' has been cancelled.",
        "dispatch": MessageToDict(target_dispatch, preserving_proto_field_name=True),
    }

@router.post("/create_dispatch")
async def api_create_dispatch(body: DispatchRequestModel):
    account = body.account.dict()
    room_name = body.room_name
    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")
    result = await ensure_dispatch(room_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result

@router.post("/cancel_dispatch")
async def api_cancel_dispatch(body: DispatchRequestModel):
    account = body.account.dict()
    room_name = body.room_name
    if not await authenticate_account(account):
        raise HTTPException(status_code=401, detail="Account authentication failed")
    result = await cancel_dispatch(room_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result