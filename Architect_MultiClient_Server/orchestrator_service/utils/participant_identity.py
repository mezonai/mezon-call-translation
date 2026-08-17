"""
Participant identity utilities
"""

import copy
import json
import re
from collections import defaultdict
from typing import Any

from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


def parse_participant_identity(participant_identity: str) -> str:
    """
    Parse participant identity to extract extName if it's a JSON string.

    If participant_identity is a JSON string with "extName" field, return the sanitized extName value.
    Otherwise, return the original participant_identity.
    """
    if not participant_identity:
        return participant_identity

    try:
        data = json.loads(participant_identity)

        if isinstance(data, dict) and "extName" in data:
            ext_name = data["extName"]

            if isinstance(ext_name, str):
                sanitized = re.sub(r"[^a-zA-Z0-9_-]+", "_", ext_name)

                # If sanitized becomes empty → fallback
                if sanitized:
                    logger.debug(f"Sanitized extName: '{sanitized}'")
                    return sanitized
                else:
                    logger.debug("extName sanitized to empty, fallback to original")
                    return "unknown_participant"

        # JSON parsed but no valid extName
        logger.debug("JSON parsed but no valid extName, keeping original")
        return participant_identity

    except (json.JSONDecodeError, TypeError, ValueError):
        logger.debug("Not JSON format, keeping original participant_identity")
        return participant_identity

def mask_messages(messages: list[dict[str, Any]], id_to_username: dict[str, str]) -> list[dict[str, Any]]: # type: ignore[explicit-any]
    masked = copy.deepcopy(messages)
    for msg in masked:
        p_id = str(msg.get("participant_id"))
        if p_id in id_to_username:
            msg["participant_id"] = id_to_username[p_id]
    return masked

def decode_aliases(text: str, username_to_id: dict[str, str]) -> str:
    if not text:
        return text
    for alias, real_id in sorted(username_to_id.items(), key=lambda x: len(x[0]), reverse=True):
        text = text.replace(alias, real_id)
    return text

def sanitize_and_decode_list(items: list[str], username_to_id: dict[str, str], require_brackets: bool = False) -> list[str]:
    if not items:
        return []

    result = []
    for item in items:
        cleaned = item.strip()
        if not cleaned:
            continue

        if require_brackets:
            if "[" not in cleaned or "]" not in cleaned:
                continue
        else:
            if cleaned.startswith("//"):
                continue
            if len(cleaned) <= 5 and not cleaned.isalnum():
                continue

        decoded = decode_aliases(cleaned, username_to_id)
        result.append(decoded)

    return result

def group_next_focus_by_user(next_focus_list: list[str]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = defaultdict(list)

    pattern = re.compile(r'^\[([^\]]+)\]\s*[:-]?\s*(.*)')

    for item in next_focus_list or []:
        cleaned_item = item.strip()
        if not cleaned_item:
            continue

        match = pattern.match(cleaned_item)
        if match:
            user_id = match.group(1).strip()
            task = match.group(2).strip()

            task = re.sub(r'^[-*]\s*', '', task).strip()

            if task:
                grouped[user_id].append(task)
        else:
            grouped["general"].append(cleaned_item)

    return dict(grouped)

def build_username_maps( # type: ignore[explicit-any]
    unique_participants: list[str],
    room_participants: list[dict[str, Any]]
) -> tuple[dict[str, str], dict[str, str]]:

    identity_username_lookup = {}
    for p in (room_participants or []):
        pid = p.get("participant_identity")
        uname = p.get("username")
        if pid and uname:
            identity_username_lookup[pid] = uname

    id_to_username = {}
    username_to_id = {}
    fallback_idx = 0

    for p_id in unique_participants:
        p_id_str = str(p_id)
        username = identity_username_lookup.get(p_id_str)
        if not username:
            fallback_idx += 1
            username = f"user-{fallback_idx}"
        id_to_username[p_id_str] = username
        username_to_id[username] = p_id_str

    return id_to_username, username_to_id

def format_key_discussions(discussions: list[str]) -> list[str]:
    if not discussions:
        return []

    formatted = []
    pattern = re.compile(r'^\[([^\]]+)\]\s*(.*)')

    for item in discussions:
        cleaned_item = item.strip()
        if not cleaned_item:
            continue

        match = pattern.match(cleaned_item)
        if match:
            title = match.group(1).strip()
            content = match.group(2).strip()

            formatted.append(f"- {title}: {content}")
        else:
            formatted.append(f"- {cleaned_item}" if not cleaned_item.startswith("-") else cleaned_item)

    return formatted

