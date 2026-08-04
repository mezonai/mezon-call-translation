"""gRPC adapter: today's AudioSource (PLAN.md D3/D4).

This is the only place that knows the frames come from `agents` over gRPC.
The driving contract onto the application layer is just three calls --
start_recording / append_audio / stop_recording -- so a future
infra/sfu/rtp_ingest.py adapter (D3: direct RTP capture once the SFU is
swapped) can drive the exact same three use cases without either of them
changing.

Graceful vs. abrupt stream end (D5 tier 2) is detected structurally: the
`async for chunk in request_iterator` loop finishing on its own means the
agent half-closed on purpose (track unpublished / shutdown); an exception
raised while iterating means the connection broke.
"""

from __future__ import annotations

import asyncio
import logging

from record_service.application.append_audio import AppendAudio
from record_service.application.start_recording import StartRecording
from record_service.application.stop_recording import StopRecording
from record_service.infra.grpc import recording_pb2, recording_pb2_grpc
from record_service.infra.naming import build_object_key

logger = logging.getLogger(__name__)


class RecordingIngestServicer(recording_pb2_grpc.RecordingIngestServicer):
    def __init__(
        self,
        start_recording: StartRecording,
        append_audio: AppendAudio,
        stop_recording: StopRecording,
        minio_bucket: str,
    ) -> None:
        self._start_recording = start_recording
        self._append_audio = append_audio
        self._stop_recording = stop_recording
        self._minio_bucket = minio_bucket

    async def StreamAudio(self, request_iterator, context):
        session_id: str | None = None
        session = None
        graceful_close = False

        try:
            async for chunk in request_iterator:
                which = chunk.WhichOneof("payload")

                if which == "start":
                    start = chunk.start
                    object_key = build_object_key(start.room_id, start.participant_identity, start.source)
                    try:
                        session = await self._start_recording.execute(
                            room_id=start.room_id,
                            track_id=start.track_id,
                            participant_identity=start.participant_identity,
                            source=start.source,
                            sample_rate=start.sample_rate,
                            channels=start.channels,
                            bucket=self._minio_bucket,
                            object_key=object_key,
                        )
                    except Exception as exc:  # noqa: BLE001 - reject boundary, see PLAN.md D5 tier 1
                        logger.error(
                            "Rejecting start for room=%s track=%s: %s", start.room_id, start.track_id, exc
                        )
                        yield recording_pb2.RecordingAck(status="rejected", error=str(exc))
                        return
                    session_id = session.session_id
                    yield recording_pb2.RecordingAck(status="accepted", object_key=session.object_key)

                elif which == "pcm":
                    if session_id is None:
                        yield recording_pb2.RecordingAck(
                            status="rejected", error="must send SessionStart first"
                        )
                        return
                    ok = await self._append_audio.execute(session_id, chunk.pcm)
                    if not ok:
                        yield recording_pb2.RecordingAck(
                            status="rejected", error="unknown session (already finalized?)"
                        )
                        return

                elif which == "dropped":
                    if session_id is not None:
                        await self._append_audio.execute(
                            session_id, None, dropped_count=chunk.dropped.count
                        )

            graceful_close = True

        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - abrupt disconnect boundary
            logger.warning("Stream error for session %s: %s", session_id, exc)
        finally:
            if session_id is not None:
                await self._stop_recording.execute(session_id, graceful=graceful_close)

        if graceful_close and session_id is not None:
            yield recording_pb2.RecordingAck(
                status="completed", object_key=session.object_key if session else ""
            )
