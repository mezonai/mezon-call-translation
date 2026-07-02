"""
Common type definitions shared across models and services.
"""

# Redis Types
RedisValue = bytes | str | int | float | None
RedisMapping = dict[bytes | str | int | float, bytes | str | int | float]

# JSON Types
JsonValue = str | int | float | bool | None | dict[str, "JsonValue"] | list["JsonValue"]
JsonObject = dict[str, JsonValue]
