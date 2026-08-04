"""Wiring: pick concrete adapters, construct use cases (PLAN.md D4).

This is the only module allowed to know about every concrete adapter class.
Everything else (application/*, infra/grpc/ingest_server.py) depends only on
domain/ports.py -- that's what keeps infra/sfu/* a drop-in replacement for
infra/grpc/* later without touching this file's callers.
"""

from __future__ import annotations

from dataclasses import dataclass

from record_service.application.append_audio import AppendAudio
from record_service.application.recover_orphaned_sessions import RecoverOrphanedSessions
from record_service.application.report_event import ReportEvent
from record_service.application.session_registry import SessionRegistry
from record_service.application.start_recording import StartRecording
from record_service.application.stop_recording import StopRecording
from record_service.config import Config, get_config
from record_service.domain.policies import RecordingPolicy, RetryPolicy
from record_service.infra.grpc.ingest_server import RecordingIngestServicer
from record_service.infra.reporting.http_event_reporter import HttpEventReporter
from record_service.infra.state.file_session_state_repo import FileSessionStateRepository
from record_service.infra.storage.s3_blob_storage import S3BlobStorage


@dataclass
class Application:
    config: Config
    registry: SessionRegistry
    start_recording: StartRecording
    append_audio: AppendAudio
    stop_recording: StopRecording
    report_event: ReportEvent
    recover_orphaned_sessions: RecoverOrphanedSessions
    ingest_servicer: RecordingIngestServicer
    event_reporter: HttpEventReporter


def build_application(config: Config | None = None) -> Application:
    config = config or get_config()

    policy = RecordingPolicy(
        part_size_bytes=config.recording_policy.part_size_bytes,
        upload_retry=RetryPolicy(
            max_attempts=config.recording_policy.max_upload_retries,
            base_delay_seconds=config.recording_policy.upload_retry_base_delay_seconds,
        ),
        grace_period_seconds=config.recording_policy.grace_period_seconds,
        byte_rate_tolerance=config.recording_policy.byte_rate_tolerance,
        drop_rate_warning_threshold=config.recording_policy.drop_rate_warning_threshold,
        report_retry=RetryPolicy(
            max_attempts=config.orchestrator.max_report_retries,
            base_delay_seconds=config.orchestrator.report_retry_base_delay_seconds,
        ),
    )

    registry = SessionRegistry()
    blob_storage = S3BlobStorage(config.minio)
    state_repo = FileSessionStateRepository(config.state_store.directory)
    event_reporter = HttpEventReporter(config.orchestrator)

    report_event = ReportEvent(event_reporter, state_repo, policy.report_retry)
    start_recording = StartRecording(registry, blob_storage, state_repo, report_event)
    append_audio = AppendAudio(registry, blob_storage, state_repo, policy)
    stop_recording = StopRecording(registry, blob_storage, state_repo, report_event, policy)
    recover_orphaned_sessions = RecoverOrphanedSessions(registry, blob_storage, state_repo, report_event, policy)

    ingest_servicer = RecordingIngestServicer(
        start_recording, append_audio, stop_recording, config.minio.bucket
    )

    return Application(
        config=config,
        registry=registry,
        start_recording=start_recording,
        append_audio=append_audio,
        stop_recording=stop_recording,
        report_event=report_event,
        recover_orphaned_sessions=recover_orphaned_sessions,
        ingest_servicer=ingest_servicer,
        event_reporter=event_reporter,
    )
