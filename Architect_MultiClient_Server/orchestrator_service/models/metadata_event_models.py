"""
Models and enums for metadata events
"""
from enum import Enum


class MetadataEventType(str, Enum):
    """
    Enum for metadata event types.

    These events track room lifecycle and are broadcast via SSE
    and persisted to MongoDB with 3-day TTL.
    """
    ROOM_STARTED = "room_started"
    ROOM_ENDED = "room_ended"
    ROOM_RECORD_DONE = "room_record_done"
    ROOM_SUMMARY_DONE = "room_summary_done"

    @classmethod
    def values(cls):
        """Get list of all event type values"""
        return [e.value for e in cls]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        """Check if a value is a valid event type"""
        return value in cls.values()
