"""
Redis decode utilities

Centralised helpers for converting raw Redis responses (bytes) to Python
strings.  Used by hash repositories, queue discovery, and any other layer
that operates with decode_responses=False.
"""

from typing import Any


def decode_value(value: Any) -> str | None:
    """
    Decode a single Redis value to string.

    Args:
        value: Raw value returned by redis-py (bytes, str, int, …)

    Returns:
        Decoded string, or None if the input is None
    """
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


def decode_mapping(data: dict[Any, Any]) -> dict[str, str]:
    """
    Decode a Redis hash mapping (keys and values) to plain Python strings.

    Entries whose key or value decodes to None are silently dropped.

    Args:
        data: Raw dict returned by HGETALL / similar commands

    Returns:
        Dict with all keys and values as strings
    """
    result: dict[str, str] = {}
    for key, value in data.items():
        key_str = decode_value(key)
        value_str = decode_value(value)
        if key_str is not None and value_str is not None:
            result[key_str] = value_str
    return result
