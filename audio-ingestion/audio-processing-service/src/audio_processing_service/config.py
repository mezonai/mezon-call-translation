"""Environment-driven configuration for audio-processing-service.

Same dataclass + from_env() convention as record-service/src/record_service/config.py.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class RedisConfig:
    """Consumer-side Redis config -- mirrors stt_service/config/app_config.py's
    RedisConfig (audio-ingestion PLAN.md D28 point 3: reuse the existing Redis
    Stream consumer mechanism as-is, same tunables/defaults).
    """

    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    claim_min_idle_time_ms: int = 60000
    block_timeout_ms: int = 5000
    max_retries: int = 3
    max_connections: int = 10
    socket_timeout: float = 30.0
    socket_connect_timeout: float = 10.0
    heartbeat_interval_sec: float = 10.0
    worker_timeout_sec: float = 30.0

    @classmethod
    def from_env(cls) -> "RedisConfig":
        return cls(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", "6379")),
            password=os.getenv("REDIS_PASSWORD", ""),
            db=int(os.getenv("REDIS_DB", "0")),
            claim_min_idle_time_ms=int(os.getenv("REDIS_CLAIM_MIN_IDLE_TIME_MS", "60000")),
            block_timeout_ms=int(os.getenv("REDIS_BLOCK_TIMEOUT_MS", "5000")),
            max_retries=int(os.getenv("REDIS_MAX_RETRIES", "3")),
            max_connections=int(os.getenv("REDIS_MAX_CONNECTIONS", "10")),
            socket_timeout=float(os.getenv("REDIS_SOCKET_TIMEOUT", "30")),
            socket_connect_timeout=float(os.getenv("REDIS_SOCKET_CONNECT_TIMEOUT", "10")),
            heartbeat_interval_sec=float(os.getenv("REDIS_HEARTBEAT_INTERVAL_SEC", "10")),
            worker_timeout_sec=float(os.getenv("REDIS_WORKER_TIMEOUT_SEC", "30")),
        )


@dataclass
class MinIOConfig:
    """Same bucket record-service writes raw captures to (PLAN.md D28 point 2)
    -- the derivative is written back into it under a `.ogg` suffix, no
    separate bucket/prefix setup needed.
    """

    endpoint: str = ""
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    region: str = "us-east-1"
    secure: bool = False
    force_path_style: bool = True

    @classmethod
    def from_env(cls) -> "MinIOConfig":
        return cls(
            endpoint=os.getenv("MINIO_ENDPOINT", ""),
            access_key=os.getenv("MINIO_ACCESS_KEY", ""),
            secret_key=os.getenv("MINIO_SECRET_KEY", ""),
            bucket=os.getenv("MINIO_BUCKET", ""),
            region=os.getenv("MINIO_REGION", "us-east-1"),
            secure=bool(int(os.getenv("MINIO_SECURE", "0"))),
            force_path_style=bool(int(os.getenv("MINIO_FORCE_PATH_STYLE", "1"))),
        )

    def is_configured(self) -> bool:
        return bool(self.endpoint and self.access_key and self.secret_key and self.bucket)


@dataclass
class TranscodeConfig:
    """ffmpeg transcode tunables. sample_rate/channels are hardcoded to match
    record-service's fixed capture format (audio-ingestion PLAN.md D6) --
    deliberately not read per-track from DB metadata (PLAN.md D28 point 1:
    "hardcode simple trước"). bitrate/codec match the format already used by
    the old LiveKit-Egress-era pipeline (PLAN.md D20: keep the existing
    format, client/bot already integrate against it) -- recovered from
    agents' deleted audio_recording_manager.py, see git history.
    """

    ffmpeg_path: str = "ffmpeg"
    sample_rate: int = 16000
    channels: int = 1
    opus_bitrate_kbps: int = 32
    ffmpeg_timeout_seconds: float = 300.0

    @classmethod
    def from_env(cls) -> "TranscodeConfig":
        return cls(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            sample_rate=int(os.getenv("TRANSCODE_SAMPLE_RATE", "16000")),
            channels=int(os.getenv("TRANSCODE_CHANNELS", "1")),
            opus_bitrate_kbps=int(os.getenv("TRANSCODE_OPUS_BITRATE_KBPS", "32")),
            ffmpeg_timeout_seconds=float(os.getenv("TRANSCODE_FFMPEG_TIMEOUT_SECONDS", "300")),
        )


@dataclass
class OrchestratorConfig:
    base_url: str = "http://orchestrator:8000"
    events_path: str = "/api/v2/recordings/events"
    request_timeout_seconds: float = 5.0
    # Matches orchestrator's INTERNAL_API_SECRET / verify_api_key (Bearer),
    # same mechanism record-service's HttpEventReporter and
    # agents/src/services/orchestrator_client.py already use.
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        return cls(
            base_url=os.getenv("ORCHESTRATOR_BASE_URL", "http://orchestrator:8000"),
            events_path=os.getenv("RECORDING_EVENTS_PATH", "/api/v2/recordings/events"),
            request_timeout_seconds=float(os.getenv("ORCHESTRATOR_TIMEOUT_SECONDS", "5")),
            api_key=os.getenv("ORCHESTRATOR_API_KEY", ""),
        )


@dataclass
class LoggerConfig:
    level: str = "INFO"

    @classmethod
    def from_env(cls) -> "LoggerConfig":
        return cls(level=os.getenv("LOG_LEVEL", "INFO").upper())


class Config:
    """Centralized configuration singleton."""

    _instance: "Config | None" = None

    def __init__(self) -> None:
        self.redis = RedisConfig.from_env()
        self.minio = MinIOConfig.from_env()
        self.transcode = TranscodeConfig.from_env()
        self.orchestrator = OrchestratorConfig.from_env()
        self.logger = LoggerConfig.from_env()


def get_config() -> Config:
    if Config._instance is None:
        Config._instance = Config()
    return Config._instance
