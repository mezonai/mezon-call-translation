"""
JSON utilities shared across orchestrator services.
"""

import json

from orchestrator_service.models.common_types import JsonObject, JsonValue

def safe_json_loads_object(payload: str) -> JsonObject:
    """Parse JSON string and return an object dict; fallback to empty dict."""
    if not payload:
        return {}

    try:
        parsed = json.loads(payload)
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}

    return parsed if isinstance(parsed, dict) else {}
