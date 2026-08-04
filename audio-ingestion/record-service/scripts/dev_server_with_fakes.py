"""Standalone dev server wired with in-memory fakes (no real MinIO/orchestrator
needed) -- useful for local smoke-testing a gRPC client against record-service
without standing up the full stack.

Also doubles as the Phase 2 cross-repo interop check: agents/ generates its
own copy of recording_pb2 from its own copy of recording.proto, so this is
the script that actually proves the two independently generated stub sets
are wire-compatible, not just that each side compiles on its own.

Usage: python scripts/dev_server_with_fakes.py [port] [max_wait_seconds]
Exits automatically after receiving one recording.completed/failed event
(or after max_wait_seconds), printing a JSON summary to stdout.
"""

from __future__ import annotations

import asyncio
import json
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

import grpc  # noqa: E402

from record_service.application.append_audio import AppendAudio  # noqa: E402
from record_service.application.report_event import ReportEvent  # noqa: E402
from record_service.application.session_registry import SessionRegistry  # noqa: E402
from record_service.application.start_recording import StartRecording  # noqa: E402
from record_service.application.stop_recording import StopRecording  # noqa: E402
from record_service.domain.policies import RecordingPolicy  # noqa: E402
from record_service.infra.grpc import recording_pb2_grpc  # noqa: E402
from record_service.infra.grpc.ingest_server import RecordingIngestServicer  # noqa: E402
from fakes import FakeBlobStorage, FakeEventReporter, FakeSessionStateRepository  # noqa: E402


async def main() -> None:
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 50099
    max_wait = float(sys.argv[2]) if len(sys.argv) > 2 else 30.0

    policy = RecordingPolicy(part_size_bytes=1000)
    registry = SessionRegistry()
    blob_storage = FakeBlobStorage()
    state_repo = FakeSessionStateRepository()
    event_reporter = FakeEventReporter()
    report_event = ReportEvent(event_reporter, state_repo, policy.report_retry)

    start = StartRecording(registry, blob_storage, state_repo, report_event)
    append = AppendAudio(registry, blob_storage, state_repo, policy)
    stop = StopRecording(registry, blob_storage, state_repo, report_event, policy)
    servicer = RecordingIngestServicer(start, append, stop, minio_bucket="dev-bucket")

    server = grpc.aio.server()
    recording_pb2_grpc.add_RecordingIngestServicer_to_server(servicer, server)
    server.add_insecure_port(f"127.0.0.1:{port}")
    await server.start()
    print(f"READY port={port}", flush=True)

    waited = 0.0
    step = 0.2
    while not event_reporter.events and waited < max_wait:
        await asyncio.sleep(step)
        waited += step

    summary = {
        "events": event_reporter.events,
        "uploads": {
            uid: {
                "completed": u["completed"],
                "aborted": u["aborted"],
                "bytes": len(FakeBlobStorage.uploaded_bytes(blob_storage, uid)) if u["completed"] else 0,
            }
            for uid, u in blob_storage.uploads.items()
        },
    }
    print("SUMMARY " + json.dumps(summary), flush=True)

    await server.stop(grace=1)


if __name__ == "__main__":
    asyncio.run(main())
