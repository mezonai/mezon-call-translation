"""Redis decode utilities.

Copied from stt_service/utils/decode.py (audio-ingestion PLAN.md D28 point 3
-- reusing the existing Redis Stream consumer mechanism as-is).
"""

from typing import Any, Dict, Optional


def decode_value(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def decode_mapping(data: Dict[Any, Any]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    for key, value in data.items():
        key_str = decode_value(key)
        value_str = decode_value(value)
        if key_str is not None and value_str is not None:
            result[key_str] = value_str
    return result
