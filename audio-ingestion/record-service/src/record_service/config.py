"""Environment-driven configuration for record-service.

Follows the same dataclass + from_env() convention used across the repo
(see Architect_MultiClient_Server/agents/src/config/application_config.py).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class GrpcConfig:
    host: str = "0.0.0.0"
    port: int = 50051

    @classmethod
    def from_env(cls) -> "GrpcConfig":
        return cls(
            host=os.getenv("RECORD_SERVICE_GRPC_HOST", "0.0.0.0"),
            port=int(os.getenv("RECORD_SERVICE_GRPC_PORT", "50051")),
        )


@dataclass
class MinIOConfig:
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
class RecordingPolicyConfig:
    """Tunables backing domain/policies.py (RecordingPolicy). See PLAN.md D5/D11/D12.

    part_size_mb now chunks *encoded* OGG/Opus bytes, not raw PCM (PLAN.md
    D6-successor). Default is 5 -- S3/MinIO's multipart floor for a
    non-final part -- specifically to keep this as small as the protocol
    allows: since Opus compresses far more than PCM, a given byte count now
    represents much more audio time (tens of minutes at default bitrate vs.
    a few minutes of raw PCM at the old 8MB default), so the unflushed
    in-memory buffer lost on an unclean process crash covers a bigger time
    window than before. Same failure *mechanism* as always (only
    already-uploaded parts survive a crash, see domain/ports.py's
    SessionStateRepository docstring) -- just a wider window now, bounded to
    the minimum S3 allows.
    """

    part_size_mb: int = 5
    max_upload_retries: int = 3
    upload_retry_base_delay_seconds: float = 0.2
    grace_period_seconds: float = 45.0
    byte_rate_tolerance: float = 0.5
    drop_rate_warning_threshold: float = 0.1

    @classmethod
    def from_env(cls) -> "RecordingPolicyConfig":
        return cls(
            part_size_mb=int(os.getenv("RECORD_PART_SIZE_MB", "8")),
            max_upload_retries=int(os.getenv("RECORD_MAX_UPLOAD_RETRIES", "3")),
            upload_retry_base_delay_seconds=float(
                os.getenv("RECORD_UPLOAD_RETRY_BASE_DELAY_SECONDS", "0.2")
            ),
            grace_period_seconds=float(os.getenv("RECORD_GRACE_PERIOD_SECONDS", "45")),
            byte_rate_tolerance=float(os.getenv("RECORD_BYTE_RATE_TOLERANCE", "0.5")),
            drop_rate_warning_threshold=float(
                os.getenv("RECORD_DROP_RATE_WARNING_THRESHOLD", "0.1")
            ),
        )

    @property
    def part_size_bytes(self) -> int:
        return self.part_size_mb * 1024 * 1024

    def validate(self) -> bool:
        # S3 multipart requires each non-final part >= 5MB.
        return self.part_size_mb >= 5 and self.max_upload_retries >= 0


@dataclass
class TranscodeConfig:
    """ffmpeg opus-encode tunables for the in-process streaming encoder
    (PLAN.md D6-successor -- record-service encodes PCM->OGG/Opus itself on
    the live ingest path now; see infra/transcode/ffmpeg_opus_encoder.py).
    Same codec settings the now-retired audio-processing-service used
    (kept identical so the output format doesn't change for client/bot).
    """

    ffmpeg_path: str = "ffmpeg"
    opus_bitrate_kbps: int = 32
    # How long to wait for ffmpeg to flush remaining output after stdin
    # closes (session end) or after a dead encoder's reader task is awaited.
    ffmpeg_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "TranscodeConfig":
        return cls(
            ffmpeg_path=os.getenv("FFMPEG_PATH", "ffmpeg"),
            opus_bitrate_kbps=int(os.getenv("TRANSCODE_OPUS_BITRATE_KBPS", "32")),
            ffmpeg_timeout_seconds=float(os.getenv("TRANSCODE_FFMPEG_TIMEOUT_SECONDS", "30")),
        )


@dataclass
class StateStoreConfig:
    directory: str = "/data/record-service/state"

    @classmethod
    def from_env(cls) -> "StateStoreConfig":
        return cls(directory=os.getenv("RECORD_STATE_DIR", "/data/record-service/state"))


@dataclass
class OrchestratorConfig:
    base_url: str = "http://orchestrator:8000"
    events_path: str = "/api/v2/recordings/events"
    request_timeout_seconds: float = 5.0
    max_report_retries: int = 3
    report_retry_base_delay_seconds: float = 0.5
    # Matches orchestrator's INTERNAL_API_SECRET / verify_api_key (Bearer),
    # same mechanism agents/src/services/orchestrator_client.py already uses
    # for its own calls to orchestrator.
    api_key: str = ""

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        return cls(
            base_url=os.getenv("ORCHESTRATOR_BASE_URL", "http://orchestrator:8000"),
            events_path=os.getenv("RECORDING_EVENTS_PATH", "/api/v2/recordings/events"),
            request_timeout_seconds=float(os.getenv("ORCHESTRATOR_TIMEOUT_SECONDS", "5")),
            max_report_retries=int(os.getenv("RECORD_MAX_REPORT_RETRIES", "3")),
            report_retry_base_delay_seconds=float(
                os.getenv("RECORD_REPORT_RETRY_BASE_DELAY_SECONDS", "0.5")
            ),
            api_key=os.getenv("ORCHESTRATOR_API_KEY", ""),
        )


@dataclass
class ReconcilerConfig:
    interval_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "ReconcilerConfig":
        return cls(interval_seconds=float(os.getenv("RECORD_RECONCILE_INTERVAL_SECONDS", "30")))


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
        self.grpc = GrpcConfig.from_env()
        self.minio = MinIOConfig.from_env()
        self.recording_policy = RecordingPolicyConfig.from_env()
        self.transcode = TranscodeConfig.from_env()
        self.state_store = StateStoreConfig.from_env()
        self.orchestrator = OrchestratorConfig.from_env()
        self.reconciler = ReconcilerConfig.from_env()
        self.logger = LoggerConfig.from_env()

        if not self.recording_policy.validate():
            raise ValueError("Invalid RECORD_* policy configuration (check part size / retries)")


def get_config() -> Config:
    if Config._instance is None:
        Config._instance = Config()
    return Config._instance
