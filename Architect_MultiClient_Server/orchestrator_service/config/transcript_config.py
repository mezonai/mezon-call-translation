"""
Configuration for Transcript API

Contains validation constants and patterns for transcript-related data.
"""

import os
from dataclasses import dataclass

# Try to load .env file if dotenv is available
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


# ============================================================================
# Validation Constants
# ============================================================================


@dataclass
class TranscriptValidationConfig:
    """Validation constraints for transcript API"""

    # String length limits
    MIN_ROOM_NAME_LENGTH: int = 1
    MAX_ROOM_NAME_LENGTH: int = 128
    MIN_TRACK_ID_LENGTH: int = 24
    MAX_TRACK_ID_LENGTH: int = 24  # MongoDB ObjectId is exactly 24 hex characters
    MIN_EGRESS_ID_LENGTH: int = 1
    MAX_EGRESS_ID_LENGTH: int = 128
    MIN_PARTICIPANT_ID_LENGTH: int = 1
    MAX_PARTICIPANT_ID_LENGTH: int = 256
    MIN_SEARCH_QUERY_LENGTH: int = 1
    MAX_SEARCH_QUERY_LENGTH: int = 500
    MIN_STATUS_LENGTH: int = 1
    MAX_STATUS_LENGTH: int = 50

    # Regex patterns
    OBJECT_ID_PATTERN: str = r"^[a-fA-F0-9]{24}$"
    ROOM_NAME_PATTERN: str = r"^[a-zA-Z0-9_\-\.]+$"

    # Numeric limits
    MIN_LIMIT: int = 1
    MAX_LIMIT: int = 500
    DEFAULT_LIMIT: int = 100
    LIMIT_TRANSCRIPT_CHUNKS: int = 10
    MIN_SKIP: int = 0
    MAX_SKIP: int = 100000
    DEFAULT_SKIP: int = 0
    MIN_CHUNK_INDEX: int = 0
    MAX_CHUNK_INDEX: int = 100000
    MIN_TIME_SECONDS: float = 0.0
    MAX_TIME_SECONDS: float = 86400.0  # 24 hours
    MIN_CONFIDENCE: float = 0.0
    MAX_CONFIDENCE: float = 1.0

    @classmethod
    def from_env(cls) -> "TranscriptValidationConfig":
        """Create config from environment variables"""
        return cls(
            MAX_ROOM_NAME_LENGTH=int(os.getenv("TRANSCRIPT_MAX_ROOM_NAME_LENGTH", "128")),
            MAX_EGRESS_ID_LENGTH=int(os.getenv("TRANSCRIPT_MAX_EGRESS_ID_LENGTH", "128")),
            MAX_PARTICIPANT_ID_LENGTH=int(os.getenv("TRANSCRIPT_MAX_PARTICIPANT_ID_LENGTH", "256")),
            MAX_SEARCH_QUERY_LENGTH=int(os.getenv("TRANSCRIPT_MAX_SEARCH_QUERY_LENGTH", "500")),
            DEFAULT_LIMIT=int(os.getenv("TRANSCRIPT_DEFAULT_LIMIT", "100")),
            MAX_LIMIT=int(os.getenv("TRANSCRIPT_MAX_LIMIT", "500")),
            MAX_SKIP=int(os.getenv("TRANSCRIPT_MAX_SKIP", "100000")),
        )


# Global instance
VALIDATION_CONFIG = TranscriptValidationConfig.from_env()
