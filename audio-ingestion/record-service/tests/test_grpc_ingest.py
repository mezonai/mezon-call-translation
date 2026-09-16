"""End-to-end smoke test through the real generated gRPC stubs and the real
bidi-streaming servicer (infra/grpc/ingest_server.py) -- everything else is
tested against fakes directly, but this is the one test that proves the
proto wire contract and the servicer's graceful/abrupt detection actually
work over an in-process gRPC channel, not just as plain Python calls.
"""

from __future__ import annotations

import grpc

from record_service.application.append_audio import AppendAudio
from record_service.application.report_event import ReportEvent
from record_service.application.session_registry import SessionRegistry
from record_service.application.start_recording import StartRecording
from record_service.application.stop_recording import StopRecording
from record_service.domain.policies import RecordingPolicy
from record_service.infra.grpc import recording_pb2, recording_pb2_grpc
from record_service.infra.grpc.ingest_server import RecordingIngestServicer
from tests.fakes import (
    FakeBlobStorage,
    FakeEventReporter,
    FakeSessionStateRepository,
    FakeStreamEncoderFactory,
)


async def _start_server():
    policy = RecordingPolicy(part_size_bytes=1000)
    registry = SessionRegistry()
    blob_storage = FakeBlobStorage()
    state_repo = FakeSessionStateRepository()
    event_reporter = FakeEventReporter()
    report_event = ReportEvent(event_reporter, state_repo, policy.report_retry)
    encoder_factory = FakeStreamEncoderFactory()

    start = StartRecording(registry, blob_storage, state_repo, report_event, encoder_factory)
    append = AppendAudio(registry, blob_storage, state_repo, policy, encoder_factory)
    stop = StopRecording(registry, blob_storage, state_repo, report_event, policy)
    servicer = RecordingIngestServicer(start, append, stop, minio_bucket="rec")

    server = grpc.aio.server()
    recording_pb2_grpc.add_RecordingIngestServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    return server, port, blob_storage, event_reporter


def _chunk_start(**kwargs) -> recording_pb2.AudioChunk:
    return recording_pb2.AudioChunk(start=recording_pb2.SessionStart(**kwargs))


def _chunk_pcm(data: bytes) -> recording_pb2.AudioChunk:
    return recording_pb2.AudioChunk(pcm=data)


async def test_grpc_stream_accept_append_graceful_close_completes():
    server, port, blob_storage, event_reporter = await _start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = recording_pb2_grpc.RecordingIngestStub(channel)

            async def request_gen():
                yield _chunk_start(
                    room_id="room-1",
                    track_id="track-1",
                    participant_identity="alice",
                    source="mic",
                    sample_rate=16000,
                    channels=1,
                )
                yield _chunk_pcm(b"hello-")
                yield _chunk_pcm(b"world")

            acks = [ack async for ack in stub.StreamAudio(request_gen())]

        statuses = [a.status for a in acks]
        assert statuses == ["accepted", "completed"]
        assert acks[0].object_key.startswith("room-1/alice-mic-audio-")

        [(upload_id, upload)] = blob_storage.uploads.items()
        assert upload["completed"] is True
        assert blob_storage.uploaded_bytes(upload_id) == b"hello-world"
        assert event_reporter.events == [
            (f"room-1:track-1", "recording.started"),
            (f"room-1:track-1", "recording.completed"),
        ]
    finally:
        await server.stop(grace=None)


async def test_grpc_stream_rejects_pcm_before_start():
    server, port, blob_storage, event_reporter = await _start_server()
    try:
        async with grpc.aio.insecure_channel(f"127.0.0.1:{port}") as channel:
            stub = recording_pb2_grpc.RecordingIngestStub(channel)

            async def request_gen():
                yield _chunk_pcm(b"too-early")

            acks = [ack async for ack in stub.StreamAudio(request_gen())]

        assert len(acks) == 1
        assert acks[0].status == "rejected"
        assert blob_storage.uploads == {}
    finally:
        await server.stop(grace=None)
