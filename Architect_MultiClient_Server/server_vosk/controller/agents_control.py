from fastapi import APIRouter, Depends, HTTPException
from ..service.auth_service import get_current_payload
from ..service.livekit_service import ensure_dispatch
from ..service.livekit_service import cancel_dispatch
from ..models.job_cancel import CancelRequest
import os

router = APIRouter(prefix="/agent", tags=["Agent"])



@router.post("/join")
async def create_job(payload: dict = Depends(get_current_payload)):
    """
    Endpoint để agent join vào room dựa trên meeting code từ JWT token
    
    Headers:
        Authorization: Bearer <jwt_token>
        
    JWT Token phải chứa:
        - meetingCode: Mã phòng họp
        - video.roomJoin: true (quyền join room)
    
    Returns:
        {
            "status": "success",
            "result": {...},
            "meetingCode": "..."
        }
    """
    # Lấy meeting_code từ payload đã giải mã
    meeting_code = payload.get("meetingCode")
    
    if not meeting_code:
        raise HTTPException(
            status_code=400,
            detail="Meeting code missing in token"
        )
    
    # Sử dụng meeting_code làm room_name
    room_name = meeting_code
    
    # Gọi hàm ensure_dispatch với URL từ environment
    result = await ensure_dispatch(room_name)
    
    return {
        "result": result,
        "meetingCode": meeting_code,
    }

@router.post("/cancel")
async def test_cancel_dispatch(payload: dict = Depends(get_current_payload)):
    """
    Endpoint test để hủy dispatch của agent trong room.
    Không yêu cầu authentication — chỉ dùng để test.
    
    Body :
        room_name: Tên room cần hủy dispatch.
    """
    # Lấy meeting_code từ payload đã giải mã
    meeting_code = payload.get("meetingCode")
    
    if not meeting_code:
        raise HTTPException(
            status_code=400,
            detail="Meeting code missing in token"
        )
    
    # Sử dụng meeting_code làm room_name
    room_name = meeting_code
    if not room_name:
        raise HTTPException(status_code=400, detail="Missing room_name")

    result = await cancel_dispatch(room_name)
    return {
        "result": result,
        "meetingCode": room_name,
    }
