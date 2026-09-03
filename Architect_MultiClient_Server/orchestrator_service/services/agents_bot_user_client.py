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

# Display-label precedence, matching agents-bot's UserInfo semantics:
# clan_nick (how the user is known inside the clan) > display_name > username.
_LABEL_KEYS = ("clan_nick", "display_name", "username")


class AgentsBotUserClient:
    """Async client for agents-bot participant and user lookups."""

    def __init__(self, base_url: str, timeout: float = 3.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._cache: dict[str, str] = {}

    async def resolve_usernames(self, user_ids: list[str]) -> dict[str, str]:
        """
        Resolve user_ids -> {user_id: display_label}.

        Returns {} on any failure or when the gateway is not configured.
        Unknown ids simply stay absent from the result.
        """
        if not user_ids:
            return {}

        result: dict[str, str] = {}
        missing: list[str] = []
        for uid in user_ids:
            cached = self._cache.get(uid)
            if cached:
                result[uid] = cached
            else:
                missing.append(uid)

        if not missing:
            return result

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/users/batch",
                    json={"user_ids": missing},
                )
            if resp.status_code != 200:
                logger.warning(f"agents_bot_user_client: batch resolve HTTP {resp.status_code}, falling back to ids")
                return result
            data = resp.json()
            for user in data.get("users", []):
                uid = str(user.get("user_id", ""))
                label = next((user.get(k) for k in _LABEL_KEYS if user.get(k)), "")
                if uid and label:
                    self._cache[uid] = label
                    result[uid] = label
            for uid in data.get("not_found", []):
                logger.debug(f"agents_bot_user_client: user {uid} not found in agents-bot cache")
        except Exception as e:
            logger.warning(f"agents_bot_user_client: batch resolve failed ({e}), falling back to ids")

        return result

    async def get_room_participants(self, room_name: str) -> list[dict[str, str]]:
        """Lấy danh sách participants hiện có trong room từ agents-bot."""
        if not self._base_url or not room_name:
            return []
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(f"{self._base_url}/api/rooms/{room_name}/participants")
                if resp.status_code != 200:
                    logger.warning(f"agents_bot_user_client: get_room_participants HTTP {resp.status_code}")
                    return []
                data = resp.json()
                raw_participants = data.get("participants", [])

                # Chuẩn hóa dữ liệu
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


async def resolve_agents_bot_usernames(participant_ids: list[str]) -> dict[str, str]:
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

    return await client.resolve_usernames(numeric_ids)


def get_agents_bot_user_client() -> AgentsBotUserClient | None:
    """Singleton accessor. Returns None when AGENTS_BOT_BASE_URL is unset."""
    global _agents_bot_user_client
    if _agents_bot_user_client is None:
        base_url = get_config().agents_bot.base_url
        if not base_url:
            return None
        _agents_bot_user_client = AgentsBotUserClient(base_url)
    return _agents_bot_user_client


async def get_agents_bot_room_participants(room_name: str) -> list[dict[str, str]]:
    client = get_agents_bot_user_client()
    if client is None:
        return []
    return await client.get_room_participants(room_name)
