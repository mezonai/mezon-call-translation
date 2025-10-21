from google.protobuf.json_format import MessageToDict
try:
    from livekit import api
    from livekit.api import twirp_client
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False
    
from ..config import get_config


async def ensure_dispatch(room_name: str):
    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error", 
            "message": "LiveKit API not available. Please install livekit-api package."
        }
        
    cfg = get_config()

    lkapi = api.LiveKitAPI(
        url=cfg.livekit.url,
        api_key=cfg.livekit.api_key,
        api_secret=cfg.livekit.api_secret,
    )

    try:
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"LiveKit server error: {e}"}

    if any(d.agent_name == cfg.livekit.agent_name for d in dispatches):
        await lkapi.aclose()
        return {"status": "exists", "message": "Dispatch already exists"}

    dispatch = await lkapi.agent_dispatch.create_dispatch(
        api.CreateAgentDispatchRequest(
            agent_name=cfg.livekit.agent_name,
            room=room_name
        )
    )
    await lkapi.aclose()

    # ✅ Convert protobuf object -> dict
    dispatch_dict = MessageToDict(dispatch, preserving_proto_field_name=True)

    return {"status": "created", "dispatch": dispatch_dict}


async def cancel_dispatch(room_name: str):
    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error",
            "message": "LiveKit API not available. Please install livekit-api package."
        }

    cfg = get_config()

    lkapi = api.LiveKitAPI(
        url=cfg.livekit.url,
        api_key=cfg.livekit.api_key,
        api_secret=cfg.livekit.api_secret,
    )

    try:
        # Lấy danh sách dispatch hiện tại
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"LiveKit server error: {e}"}

    # Tìm dispatch đúng agent cần hủy
    target_dispatch = None
    for d in dispatches:
        if d.agent_name == cfg.livekit.agent_name:
            target_dispatch = d
            print(target_dispatch)
            break

    if not target_dispatch:
        await lkapi.aclose()
        return {
            "status": "not_found",
            "message": f"No active dispatch found for agent '{cfg.livekit.agent_name}'"
        }

    try:
        # Gọi API xóa dispatch
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