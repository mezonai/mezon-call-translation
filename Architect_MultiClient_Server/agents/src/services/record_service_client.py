"""gRPC client for forwarding raw PCM to record-service.

See audio-ingestion/PLAN.md D3: the agent is the only component that talks
to LiveKit, so it forwards already-decoded frames (it needs them for STT
anyway) rather than record-service joining the room itself. This module is
the infra/grpc counterpart living on the agents side of that gRPC contract
(audio-ingestion/record-service/proto/recording.proto).

Forwarding is strictly best-effort and non-blocking from the caller's point
of view -- PLAN.md D5: "tuyệt đối không để việc này chặn pipeline STT". If
record-service is slow or unreachable, frames get dropped (and the drop
count reported, PLAN.md D12), never awaited-on by the STT hot path.
"""

from __future__ import annotations

import asyncio
import contextlib

import grpc

from src.config.application_config import get_config
from src.logger import get_logger
from src.proto import recording_pb2, recording_pb2_grpc

logger = get_logger(__name__)

_STOP = object()  # sentinel to unblock the writer loop on close()


class RecordForwarder:
    """One forwarding session for one track. Not reused across tracks."""

    def __init__(
        self,
        stub: recording_pb2_grpc.RecordingIngestStub,
        room_id: str,
        track_id: str,
        participant_identity: str,
        source: str,
        sample_rate: int,
        channels: int,
        max_queue_size: int,
    ) -> None:
        self._stub = stub
        self._room_id = room_id
        self._track_id = track_id
        self._participant_identity = participant_identity
        self._source = source
        self._sample_rate = sample_rate
        self._channels = channels

        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._dropped_since_flush = 0
        self._call = None
        self._writer_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self._rejected = False
        self._closed = False
        self.object_key: str | None = None

    async def start(self) -> None:
        self._call = self._stub.StreamAudio()
        self._writer_task = asyncio.create_task(self._write_loop(), name=f"record-fwd-write-{self._track_id}")
        self._reader_task = asyncio.create_task(self._read_loop(), name=f"record-fwd-read-{self._track_id}")
        start_chunk = recording_pb2.AudioChunk(
            start=recording_pb2.SessionStart(
                room_id=self._room_id,
                track_id=self._track_id,
                participant_identity=self._participant_identity,
                source=self._source,
                sample_rate=self._sample_rate,
                channels=self._channels,
            )
        )
        self._queue.put_nowait(start_chunk)

    def send_audio(self, pcm: bytes) -> None:
        """Non-blocking. Safe to call from the STT hot loop every frame."""
        if self._closed or self._rejected:
            return
        try:
            if self._dropped_since_flush:
                self._queue.put_nowait(
                    recording_pb2.AudioChunk(
                        dropped=recording_pb2.DroppedFrames(count=self._dropped_since_flush)
                    )
                )
                self._dropped_since_flush = 0
            self._queue.put_nowait(recording_pb2.AudioChunk(pcm=pcm))
        except asyncio.QueueFull:
            # record-service (or the network to it) can't keep up -- drop this
            # frame rather than block STT. Reported on the next successful
            # send, or folded into the final close() if it never recovers.
            self._dropped_since_flush += 1

    async def _write_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is _STOP:
                    break
                await self._call.write(item)
        except Exception as exc:  # noqa: BLE001 - forwarding is best-effort
            logger.warning(f"record-service write loop stopped for track={self._track_id}: {exc}")
        finally:
            with contextlib.suppress(Exception):
                await self._call.done_writing()

    async def _read_loop(self) -> None:
        try:
            async for ack in self._call:
                if ack.status == "accepted":
                    self.object_key = ack.object_key
                    logger.info(f"record-service accepted track={self._track_id} -> {ack.object_key}")
                elif ack.status == "rejected":
                    self._rejected = True
                    logger.warning(f"record-service rejected track={self._track_id}: {ack.error}")
                elif ack.status == "completed":
                    logger.info(f"record-service completed track={self._track_id}")
        except Exception as exc:  # noqa: BLE001 - forwarding is best-effort
            logger.warning(f"record-service read loop stopped for track={self._track_id}: {exc}")

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True

        if self._dropped_since_flush:
            with contextlib.suppress(asyncio.QueueFull):
                self._queue.put_nowait(
                    recording_pb2.AudioChunk(
                        dropped=recording_pb2.DroppedFrames(count=self._dropped_since_flush)
                    )
                )
            self._dropped_since_flush = 0

        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(_STOP)

        if self._writer_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._writer_task, timeout=5.0)
        if self._reader_task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._reader_task, timeout=5.0)


class RecordServiceClient:
    """Process-wide channel/stub. Cheap to keep open; one per agent worker."""

    _instance: "RecordServiceClient | None" = None

    def __init__(self) -> None:
        self._config = get_config().record_service
        self._channel: grpc.aio.Channel | None = None
        self._stub: recording_pb2_grpc.RecordingIngestStub | None = None

    @classmethod
    def get_instance(cls) -> "RecordServiceClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_stub(self) -> recording_pb2_grpc.RecordingIngestStub:
        if self._stub is None:
            self._channel = grpc.aio.insecure_channel(self._config.grpc_addr)
            self._stub = recording_pb2_grpc.RecordingIngestStub(self._channel)
        return self._stub

    async def new_forwarder(
        self,
        room_id: str,
        track_id: str,
        participant_identity: str,
        source: str,
        sample_rate: int,
        channels: int,
    ) -> RecordForwarder | None:
        """Returns None (and logs) if starting the forwarder fails -- callers
        must treat that as "no recording this session", not an error."""
        try:
            forwarder = RecordForwarder(
                stub=self._get_stub(),
                room_id=room_id,
                track_id=track_id,
                participant_identity=participant_identity,
                source=source,
                sample_rate=sample_rate,
                channels=channels,
                max_queue_size=self._config.max_queue_size,
            )
            await forwarder.start()
            return forwarder
        except Exception as exc:  # noqa: BLE001 - never let this break STT startup
            logger.error(f"Failed to start record-service forwarder for track={track_id}: {exc}")
            return None

    async def close(self) -> None:
        if self._channel is not None:
            await self._channel.close()
            self._channel = None
            self._stub = None


def get_record_service_client() -> RecordServiceClient:
    return RecordServiceClient.get_instance()
