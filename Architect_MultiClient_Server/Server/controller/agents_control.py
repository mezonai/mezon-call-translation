from fastapi import APIRouter, Depends, HTTPException
from ..models.job_request import JobRequest
from ..service.auth_service import get_current_payload
from ..service.livekit_service import ensure_dispatch

router = APIRouter(prefix="/agent", tags=["Agent"])

@router.post("/join")
async def create_job(body: JobRequest, payload=Depends(get_current_payload)):
    video_info = payload.get("video")
    if not video_info or not video_info.get("roomJoin"):
        raise HTTPException(status_code=403, detail="Client not allowed to join room")

    room_name = video_info.get("room")
    if not room_name:
        raise HTTPException(status_code=400, detail="Room name missing in token")

    result = await ensure_dispatch(room_name, body.url)
    return {"result": result}
