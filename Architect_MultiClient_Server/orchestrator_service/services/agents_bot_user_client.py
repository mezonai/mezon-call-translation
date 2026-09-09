"""
Read-side HTTP client for agents-bot's participant-roster and user APIs.

agents-bot is the only service that sees Mezon identity events
(VoiceJoinedEvent, ChannelMessage), so it is the only place that holds the
user_id -> username mapping. The old Python agents embedded the display
name in the LiveKit identity (extName) and the orchestrator harvested it
from there; the new Go agents keep participant_identity as the stable
numeric user_id, so the orchestrator resolves names at render time via
this client instead.

Design constraints:
- Strictly best-effort: any failure (unconfigured, network, bad payload)
  returns an empty result and never raises. Participant lookup must not make
  room registration fail, and username callers retain their own fallbacks.
- In-memory per-process cache so repeated summary/retry flows for the same
  room don't re-hit agents-bot.
"""

import httpx

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)

_MAX_CONNECTIONS = 20
_MAX_KEEPALIVE_CONNECTIONS = 10


class AgentsBotUserClient:
    """Async client for agents-bot participant and user lookups."""

    def __init__(self, base_url: str, timeout: float = 3.0):
        self._base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=httpx.Timeout(timeout),
            limits=httpx.Limits(
                max_connections=_MAX_CONNECTIONS,
                max_keepalive_connections=_MAX_KEEPALIVE_CONNECTIONS,
            ),
        )
        # Names are room-sensitive because agents-bot resolves each room's clan
        # internally. The global key deliberately bypasses clan nicknames.
        self._cache: dict[tuple[str, str], str] = {}

    async def close(self) -> None:
        """Close the shared HTTP connection pool."""
        if not self._http.is_closed:
            await self._http.aclose()

    async def resolve_usernames(
        self,
        user_ids: list[str],
        *,
        room_name: str | None = None,
    ) -> dict[str, str]:
        """
        Resolve user_ids -> {user_id: display_label} for an optional room.

        Returns {} on any failure or when the gateway is not configured.
        Unknown ids simply stay absent from the result.
        """
        if not user_ids:
            return {}

        room_name = room_name.strip() if room_name else None
        context_key = f"room:{room_name}" if room_name else "global"

        result: dict[str, str] = {}
        missing: list[str] = []
        for uid in user_ids:
            cached = self._cache.get((context_key, uid))
            if cached:
                result[uid] = cached
            else:
                missing.append(uid)

        if not missing:
            return result

        try:
            payload: dict[str, object] = {"user_ids": missing}
            if room_name:
                payload["room_name"] = room_name

            resp = await self._http.post(
                "/api/users",
                json=payload,
            )
            if resp.status_code != 200:
                logger.error(f"agents_bot_user_client: batch resolve HTTP {resp.status_code}, falling back to ids")
                return result
            data = resp.json()
            context_resolved = not room_name or data.get("context_resolved") is True
            if room_name and not context_resolved:
                logger.warning(
                    f"agents_bot_user_client: clan context unresolved for room '{room_name}', "
                    "using generic display labels"
                )
            for user in data.get("users", []):
                uid = str(user.get("user_id", ""))
                raw_label = user.get("display_label")
                label = raw_label.strip() if isinstance(raw_label, str) else ""
                if uid and label:
                    # Do not permanently cache a generic fallback for a room
                    # whose clan mapping has not reached agents-bot yet.
                    if context_resolved:
                        self._cache[(context_key, uid)] = label
                    result[uid] = label
            for uid in data.get("not_found", []):
                logger.warning(f"agents_bot_user_client: user {uid} not found in agents-bot cache")
        except Exception as e:
            logger.warning(f"agents_bot_user_client: batch resolve failed ({e}), falling back to ids")

        return result

    async def send_chat_message(self, room_name: str, message: str) -> bool:
        """
        Send a message into a room's Mezon chat via agents-bot, as the bot.

        Part of the send_chat_message agent-request flow
        (mezon-sfu-migration-plan.md): orchestrator calls agents-bot
        directly for this one request type instead of relaying through the
        per-room Go agent's SSE listener (unlike tts_play/transcript_control)
        -- agents-bot is the only thing holding a live Mezon bot session,
        so there's no per-room agent state this needs to go through first.

        Unlike resolve_usernames/get_room_participants above (read-side,
        best-effort, degrade to {}/[] on any failure), this is a real write
        with a user-visible effect in a live meeting -- callers need to know
        whether it actually landed, so this does NOT swallow errors the same
        way:
        - Returns False only for the one *expected* non-delivery case --
          agents-bot doesn't currently consider room_name active (HTTP 409,
          mirrors the SSE path's "no active agent connection" case in
          AgentRequestChannel.send_request).
        - Raises for everything else (agents-bot unreachable, timeout, the
          Mezon send itself failing) -- a genuine failure, not a soft
          "nobody was listening" outcome.
        """
        resp = await self._http.post(f"/api/rooms/{room_name}/chat", json={"message": message})
        if resp.status_code == 409:
            logger.warning(f"agents_bot_user_client: send_chat_message room {room_name} not active on agents-bot")
            return False
        if resp.status_code != 200:
            raise RuntimeError(f"agents_bot_user_client: send_chat_message HTTP {resp.status_code}: {resp.text}")

        logger.info(f"agents_bot_user_client: send_chat_message sent to room {room_name} ({len(message)} chars)")
        return True

    async def get_room_participants(self, room_name: str) -> list[dict[str, str]]:
        """Get list participants in room from agents-bot."""
        if not self._base_url or not room_name:
            return []
        try:
            resp = await self._http.get(f"/api/rooms/{room_name}/participants")
            if resp.status_code != 200:
                logger.warning(f"agents_bot_user_client: get_room_participants HTTP {resp.status_code}")
                return []
            data = resp.json()
            raw_participants = data.get("participants", [])

            # data normalization
            result = []
            seen = set()
            for p in raw_participants:
                if not isinstance(p, dict):
                    continue
                identity = str(p.get("participant_identity") or "").strip()
                raw_username = p.get("username")
                username = raw_username.strip() if isinstance(raw_username, str) else ""
                if identity and identity not in seen:
                    seen.add(identity)
                    item = {"participant_identity": identity}
                    if username:
                        item["username"] = username
                    result.append(item)
            return result
        except Exception as e:
            logger.warning(f"agents_bot_user_client: failed to fetch room participants ({e})")
            return []


_agents_bot_user_client: AgentsBotUserClient | None = None


async def resolve_agents_bot_usernames(
    participant_ids: list[str],
    *,
    room_name: str | None = None,
) -> dict[str, str]:
    """
    Convenience wrapper used by summary flows.

    Only numeric user_ids are sent (the new Go-agent flow keeps
    participant_identity as the raw Mezon user_id; bots use EG_/other
    prefixes that can never hit the gateway's cache). Never raises --
    callers treat {} as "no gateway info, use fallbacks".
    """
    numeric_ids = [str(p) for p in participant_ids if str(p).isdigit()]
    if not numeric_ids:
        return {}

    client = get_agents_bot_user_client()
    if client is None:
        return {}

    return await client.resolve_usernames(numeric_ids, room_name=room_name)


def get_agents_bot_user_client() -> AgentsBotUserClient | None:
    """Singleton accessor. Returns None when the configured base URL is empty."""
    global _agents_bot_user_client
    if _agents_bot_user_client is None:
        base_url = get_config().agents_bot.base_url
        if not base_url:
            return None
        _agents_bot_user_client = AgentsBotUserClient(base_url)
    return _agents_bot_user_client


async def close_agents_bot_user_client() -> None:
    """Close and reset the shared agents-bot HTTP client."""
    global _agents_bot_user_client
    if _agents_bot_user_client is not None:
        await _agents_bot_user_client.close()
        _agents_bot_user_client = None


async def get_agents_bot_room_participants(room_name: str) -> list[dict[str, str]]:
    client = get_agents_bot_user_client()
    if client is None:
        return []
    return await client.get_room_participants(room_name)


async def send_agents_bot_chat_message(room_name: str, message: str) -> bool:
    """
    Convenience wrapper for the send_chat_message agent-request flow (see
    AgentRequestChannel.send_request). Unlike the resolve/get_participants
    wrappers above, this raises rather than degrading to a default value
    when agents-bot isn't configured at all -- an unconfigured gateway
    silently dropping a chat-send request is worse than a loud failure.
    """
    client = get_agents_bot_user_client()
    if client is None:
        raise RuntimeError("agents_bot_user_client: send_chat_message failed, AGENTS_BOT_BASE_URL not configured")
    return await client.send_chat_message(room_name, message)
