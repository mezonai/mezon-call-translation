"""In-memory fakes for the ports, used to test application/* logic without
touching real S3/HTTP (PLAN.md D4 -- this is the payoff: the use cases don't
care what implements the ports)."""

from __future__ import annotations

from dataclasses import dataclass, field

from record_service.domain.models import RecordingSession, UploadedPart
from record_service.domain.ports import (
    BlobStorage,
    EventReporter,
    SessionStateRepository,
    StreamEncoder,
    StreamEncoderFactory,
)


@dataclass
class FakeBlobStorage(BlobStorage):
    fail_create: bool = False
    fail_upload_part_times: int = 0
    fail_complete: bool = False
    uploads: dict = field(default_factory=dict)  # upload_id -> {"parts": {n: bytes}, "completed": bool, "aborted": bool}
    _next_id: int = 0

    async def create_multipart_upload(self, bucket: str, key: str) -> str:
        if self.fail_create:
            raise RuntimeError("simulated create_multipart_upload failure")
        self._next_id += 1
        upload_id = f"upload-{self._next_id}"
        self.uploads[upload_id] = {"parts": {}, "completed": False, "aborted": False}
        return upload_id

    async def upload_part(self, bucket, key, upload_id, part_number, data) -> str:  # noqa: ANN001
        if self.fail_upload_part_times > 0:
            self.fail_upload_part_times -= 1
            raise RuntimeError("simulated transient upload_part failure")
        self.uploads[upload_id]["parts"][part_number] = data
        return f"etag-{part_number}"

    async def complete_multipart_upload(self, bucket, key, upload_id, parts) -> None:  # noqa: ANN001
        if self.fail_complete:
            raise RuntimeError("simulated complete_multipart_upload failure")
        self.uploads[upload_id]["completed"] = True

    async def abort_multipart_upload(self, bucket, key, upload_id) -> None:  # noqa: ANN001
        self.uploads[upload_id]["aborted"] = True

    def uploaded_bytes(self, upload_id: str) -> bytes:
        parts = self.uploads[upload_id]["parts"]
        return b"".join(parts[n] for n in sorted(parts))


class FakeSessionStateRepository(SessionStateRepository):
    def __init__(self) -> None:
        self.store: dict[str, RecordingSession] = {}

    async def save(self, session: RecordingSession) -> None:
        self.store[session.session_id] = session

    async def delete(self, session_id: str) -> None:
        self.store.pop(session_id, None)

    async def list_unfinished(self) -> list[RecordingSession]:
        return list(self.store.values())


class FakeStreamEncoder(StreamEncoder):
    """Identity pass-through -- returns exactly what it's fed, unchanged, so
    existing tests' byte-for-byte upload assertions don't need to know
    anything about real Opus encoding."""

    def __init__(self, start_alive: bool = True) -> None:
        self._pending = bytearray()
        self._alive = start_alive

    async def feed(self, pcm: bytes) -> None:
        if not self._alive:
            raise RuntimeError("fake encoder is dead")
        self._pending.extend(pcm)

    async def drain(self) -> bytes:
        data = bytes(self._pending)
        self._pending.clear()
        return data

    async def close(self) -> bytes:
        return await self.drain()

    @property
    def alive(self) -> bool:
        return self._alive


@dataclass
class FakeStreamEncoderFactory(StreamEncoderFactory):
    """dead_on_arrival_count: the first N created encoders start already
    dead (simulating ffmpeg failing to start/dying immediately), to exercise
    AppendAudio's restart-on-death path. Encoders after that are healthy."""

    dead_on_arrival_count: int = 0
    created: list = field(default_factory=list)

    async def create(self, sample_rate: int, channels: int) -> StreamEncoder:
        start_alive = len(self.created) >= self.dead_on_arrival_count
        encoder = FakeStreamEncoder(start_alive=start_alive)
        self.created.append(encoder)
        return encoder


class FakeEventReporter(EventReporter):
    def __init__(self, fail_times: int = 0) -> None:
        self.fail_times = fail_times
        self.events: list[tuple[str, str]] = []  # (session_id, event)

    async def report(self, session: RecordingSession, event: str) -> bool:
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("simulated orchestrator unreachable")
        self.events.append((session.session_id, event))
        return True
