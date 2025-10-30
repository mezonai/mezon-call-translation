from google.protobuf.json_format import MessageToDict
try:
    from livekit import api
    from livekit.api import twirp_client
    LIVEKIT_AVAILABLE = True
except ImportError:
    LIVEKIT_AVAILABLE = False
    
from ..config import get_config


async def ensure_dispatch(room_name: str):
    """
    Đảm bảo rằng đã tồn tại một Agent Dispatch cho phòng LiveKit cụ thể.
    - Nếu chưa tồn tại → tạo mới dispatch.
    - Nếu đã có → trả về trạng thái "exists".
    - Nếu LiveKit API không sẵn sàng → trả lỗi.
    """
    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error", 
            "message": "LiveKit API not available. Please install livekit-api package."
        }
    # Lấy cấu hình hệ thống (chứa URL, API key, secret)    
    cfg = get_config()
    # Khởi tạo client LiveKit để gọi API agent_dispatch
    lkapi = api.LiveKitAPI(
        url=cfg.livekit.url,
        api_key=cfg.livekit.api_key,
        api_secret=cfg.livekit.api_secret,
    )

    try:
        # Lấy danh sách dispatch hiện có của phòng
        # Hàm list_dispatch() lấy tất cả dispatch trong room, xem thêm LiveKit 
        # docs: https://docs.livekit.io/agents/worker/agent-dispatch/
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        await lkapi.aclose()
        return {"status": "error", "message": f"LiveKit server error: {e}"}

    # Kiểm tra trên danh sách dispatch xem đã có dispath nào dành cho agent của mình không
    # dựa vào agent_name đi kèm trong dispath
    # Nếu như có thì sẽ trả về đã tồn tại
    if any(d.agent_name == cfg.livekit.agent_name for d in dispatches):
        await lkapi.aclose()
        return {"status": "exists", "message": "Dispatch already exists"}

    # Nếu những chưa có dispatch thì sẽ tạo mới
    # kèm theo agent_name và room 
    # với agent_name là tên agent cần điều phối
    # với room là phòng cần tạo job
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
    # room_name (str): Tên của phòng LiveKit cần hủy dispatch.
    """
    Hủy (cancel) Agent Dispatch đang hoạt động trong phòng LiveKit cụ thể.
    Mô tả:
        Hàm này đảm bảo hủy dispatch đã được tạo trước đó cho agent tương ứng
        trong phòng LiveKit. Nếu dispatch không tồn tại, hàm trả về trạng thái "not_found".
    """
    # Kiểm tra xem LiveKit API có sẵn sàng không
    if not LIVEKIT_AVAILABLE:
        return {
            "status": "error",
            "message": "LiveKit API not available. Please install livekit-api package."
        }

    # Lấy cấu hình hệ thống (URL, API key, secret)
    cfg = get_config()

    # Khởi tạo client LiveKit để thao tác với API agent_dispatch
    lkapi = api.LiveKitAPI(
        url=cfg.livekit.url,
        api_key=cfg.livekit.api_key,
        api_secret=cfg.livekit.api_secret,
    )

    try:
        # Lấy danh sách dispatch hiện tại trong phòng
        dispatches = await lkapi.agent_dispatch.list_dispatch(room_name=room_name)
    except twirp_client.TwirpError as e:
        # Nếu lỗi từ phía LiveKit server → đóng kết nối và trả lỗi
        await lkapi.aclose()
        # {"status": "error"} nếu LiveKit API không sẵn sàng hoặc gặp lỗi server.
        return {"status": "error", "message": f"LiveKit server error: {e}"}

    # ✅ Tìm dispatch đúng agent cần hủy (theo agent_name trong config)
    target_dispatch = None
    for d in dispatches:
        if d.agent_name == cfg.livekit.agent_name:
            target_dispatch = d
            break

    # Nếu không tìm thấy dispatch của agent này → báo không tồn tại
    if not target_dispatch:
        await lkapi.aclose()
        # {"status": "not_found"} nếu không có dispatch nào tương ứng.
        return {
            "status": "not_found",
            "message": f"No active dispatch found for agent '{cfg.livekit.agent_name}'"
        }

    try:
        # Gọi API LiveKit để hủy (delete) dispatch tương ứng
        await lkapi.agent_dispatch.delete_dispatch(
            target_dispatch.id,
            target_dispatch.room,
        )
    except twirp_client.TwirpError as e:
        # Nếu có lỗi khi gọi API delete → đóng kết nối và trả lỗi
        await lkapi.aclose()
        return {"status": "error", "message": f"Failed to cancel dispatch: {e}"}

    # Đóng kết nối API sau khi hoàn tất
    await lkapi.aclose()

    # ✅ Trả về thông tin dispatch đã bị hủy (convert từ protobuf -> dict)
    # {"status": "cancelled", "dispatch": {...}} nếu hủy thành công.
    return {
        "status": "cancelled",
        "message": f"Dispatch for agent '{target_dispatch.agent_name}' has been cancelled.",
        "dispatch": MessageToDict(target_dispatch, preserving_proto_field_name=True),
    }
