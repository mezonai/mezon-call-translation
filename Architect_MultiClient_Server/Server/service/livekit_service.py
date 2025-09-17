from google.protobuf.json_format import MessageToDict
from livekit import api
from ..config import get_config
from livekit.api import twirp_client


async def ensure_dispatch(room_name: str, url: str = None):
    cfg = get_config()

    lkapi = api.LiveKitAPI(
        url=url or cfg.livekit.url,
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
