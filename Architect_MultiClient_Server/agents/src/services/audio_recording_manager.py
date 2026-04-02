"""
Audio recording manager for track-based multipart uploads to MinIO/S3.

This manager receives PCM chunks for a track and uploads parts incrementally
using the S3 multipart API. The final part is completed when recording stops.
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Dict
from urllib.parse import urlparse

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from src.config.application_config import get_config
from src.logger import get_logger


logger = get_logger(__name__)


@dataclass
class MultipartRecordingSession:
    """Mutable state for one track recording multipart upload."""

    track_id: str
    bucket: str
    object_key: str
    upload_id: str
    part_number: int = 1
    etags: list[Dict[str, Any]] = field(default_factory=list)
    buffer: bytearray = field(default_factory=bytearray)
    total_uploaded_bytes: int = 0
    raw_input_bytes: int = 0
    started_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    encoder_process: asyncio.subprocess.Process | None = None
    encoder_stdout_task: asyncio.Task | None = None
    encoder_stderr_task: asyncio.Task | None = None
    encoder_stderr_tail: str = ""
    encoder_failed: bool = False


class AudioRecordingManager:
    """Handles track recording sessions and multipart uploads to MinIO."""

    def __init__(self):
        self.config = get_config()
        self._sessions: Dict[str, MultipartRecordingSession] = {}
        self._sessions_lock = asyncio.Lock()
        self._s3_client = None

    def _is_enabled(self) -> bool:
        return self.config.recording_upload.enabled and self.config.minio.is_configured()

    @staticmethod
    def _is_no_such_upload(exc: Exception) -> bool:
        if not isinstance(exc, ClientError):
            return False
        error = exc.response.get("Error", {})
        return error.get("Code") == "NoSuchUpload"

    @staticmethod
    def _trim_text(text: str, max_len: int = 1000) -> str:
        if len(text) <= max_len:
            return text
        return text[-max_len:]

    def _get_s3_client(self):
        if self._s3_client is None:
            addressing_style = "path" if self.config.minio.force_path_style else "virtual"
            self._s3_client = boto3.client(
                "s3",
                endpoint_url=self.config.minio.endpoint,
                aws_access_key_id=self.config.minio.access_key,
                aws_secret_access_key=self.config.minio.secret_key,
                region_name=self.config.minio.region,
                use_ssl=self.config.minio.secure,
                config=BotoConfig(
                    retries={"max_attempts": self.config.recording_upload.max_retries + 1},
                    s3={"addressing_style": addressing_style},
                ),
            )
        return self._s3_client

    def _resolve_target(self, file_output_path: str) -> tuple[str, str]:
        """Resolve bucket/object_key from either s3:// URL or plain key path."""
        if file_output_path.startswith("s3://"):
            parsed = urlparse(file_output_path)
            bucket = parsed.netloc.strip()
            object_key = parsed.path.lstrip("/")
            if not bucket or not object_key:
                raise ValueError(f"Invalid s3 path: {file_output_path}")
            return bucket, object_key

        bucket = self.config.minio.bucket
        object_key = file_output_path.lstrip("/")
        if not object_key:
            raise ValueError("file_output_path must not be empty")
        return bucket, object_key

    async def has_active_recording(self, track_id: str) -> bool:
        async with self._sessions_lock:
            return track_id in self._sessions

    async def start_recording(self, track_id: str, file_output_path: str) -> bool:
        """Create multipart upload session for track_id."""
        if not self._is_enabled():
            logger.warning(
                "[Recording] Upload disabled or MinIO config missing; "
                f"cannot start recording track={track_id}"
            )
            return False

        bucket, object_key = self._resolve_target(file_output_path)

        async with self._sessions_lock:
            if track_id in self._sessions:
                logger.info(f"[Recording] Session already active for track={track_id}")
                return True

        client = self._get_s3_client()
        response = await asyncio.to_thread(
            client.create_multipart_upload,
            Bucket=bucket,
            Key=object_key,
            ContentType="audio/ogg",
        )
        upload_id = response["UploadId"]

        session = MultipartRecordingSession(
            track_id=track_id,
            bucket=bucket,
            object_key=object_key,
            upload_id=upload_id,
        )

        try:
            await self._start_encoder(session)
        except Exception as e:
            logger.error(f"[Recording] Failed to start Opus encoder for track={track_id}: {e}")
            await self._abort_with_session(track_id, session)
            return False

        async with self._sessions_lock:
            self._sessions[track_id] = session

        logger.info(
            f"[Recording] Started multipart upload track={track_id}, "
            f"target=s3://{bucket}/{object_key}, upload_id={upload_id}"
        )
        return True

    async def append_audio(self, track_id: str, audio_chunk: bytes) -> bool:
        """Feed raw PCM16 chunks to ffmpeg encoder stdin."""
        if not audio_chunk:
            return True

        async with self._sessions_lock:
            session = self._sessions.get(track_id)

        if not session:
            return False

        proc = session.encoder_process
        if not proc or not proc.stdin:
            return False

        async with session.write_lock:
            if proc.returncode is not None:
                session.encoder_failed = True
                logger.error(
                    f"[Recording] Encoder exited unexpectedly for track={track_id}, "
                    f"returncode={proc.returncode}, stderr={session.encoder_stderr_tail}"
                )
                return False

            try:
                proc.stdin.write(audio_chunk)
                await proc.stdin.drain()
                session.raw_input_bytes += len(audio_chunk)
                return True
            except (BrokenPipeError, ConnectionResetError) as e:
                session.encoder_failed = True
                logger.error(
                    f"[Recording] Encoder input pipe failed for track={track_id}: {e}, "
                    f"stderr={session.encoder_stderr_tail}"
                )
                return False

    async def stop_recording(self, track_id: str) -> bool:
        """Flush final bytes and complete multipart upload for track_id."""
        async with self._sessions_lock:
            # Pop first to make stop_recording idempotent and avoid duplicate completion
            # when multiple lifecycle paths race on the same track.
            session = self._sessions.pop(track_id, None)

        if not session:
            return False

        try:
            await self._close_encoder_stdin(session)
            await self._wait_encoder_drain(session)

            if session.encoder_failed:
                raise RuntimeError(
                    f"Opus encoder failed for track={track_id}. stderr={session.encoder_stderr_tail}"
                )

            async with session.lock:
                # Flush remaining encoded bytes as final multipart part.
                if session.buffer:
                    await self._upload_part_locked(session, bytes(session.buffer))
                    session.buffer.clear()

                # No data was uploaded for this session; abort cleanly.
                if not session.etags:
                    await self._abort_with_session(track_id, session)
                    logger.info(
                        f"[Recording] No audio data for track={track_id}, multipart upload aborted"
                    )
                    return True

                await self._complete_upload_locked(session)

            elapsed = max(0.0, time.time() - session.started_at)
            logger.info(
                f"[Recording] Completed track={track_id}, "
                f"raw_bytes={session.raw_input_bytes}, encoded_bytes={session.total_uploaded_bytes}, "
                f"duration={elapsed:.2f}s, "
                f"parts={len(session.etags)}"
            )
            return True
        except Exception as e:
            if self._is_no_such_upload(e):
                logger.warning(
                    f"[Recording] Multipart already finalized for track={track_id} "
                    f"(NoSuchUpload on complete)"
                )
                return True
            logger.error(f"[Recording] Failed to complete recording track={track_id}: {e}")
            await self._abort_with_session(track_id, session)
            return False

    async def abort_recording(self, track_id: str) -> bool:
        """Abort multipart upload for a track session."""
        async with self._sessions_lock:
            session = self._sessions.pop(track_id, None)

        if not session:
            return False

        await self._abort_with_session(track_id, session)
        return True

    async def stop_all_recordings(self) -> None:
        """Best-effort stop for all active recording sessions."""
        async with self._sessions_lock:
            track_ids = list(self._sessions.keys())

        for track_id in track_ids:
            try:
                await self.stop_recording(track_id)
            except Exception as e:
                logger.error(f"[Recording] stop_all failed for track={track_id}: {e}")

    async def _upload_part_locked(self, session: MultipartRecordingSession, payload: bytes) -> None:
        """Upload one multipart part. Caller must hold session.lock."""
        client = self._get_s3_client()
        max_retries = self.config.recording_upload.max_retries

        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.to_thread(
                    client.upload_part,
                    Bucket=session.bucket,
                    Key=session.object_key,
                    PartNumber=session.part_number,
                    UploadId=session.upload_id,
                    Body=payload,
                )
                etag = response["ETag"]
                session.etags.append({"ETag": etag, "PartNumber": session.part_number})
                session.total_uploaded_bytes += len(payload)
                session.part_number += 1
                return
            except Exception:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(0.2 * (2 ** attempt))

    async def _append_encoded_chunk(self, session: MultipartRecordingSession, chunk: bytes) -> None:
        """Append encoded OGG data and upload full multipart chunks."""
        if not chunk:
            return

        part_size = self.config.recording_upload.part_size_bytes

        async with session.lock:
            session.buffer.extend(chunk)

            while len(session.buffer) >= part_size:
                payload = bytes(session.buffer[:part_size])
                del session.buffer[:part_size]
                await self._upload_part_locked(session, payload)

    async def _consume_encoder_stdout(self, session: MultipartRecordingSession) -> None:
        """Read ffmpeg stdout and stream encoded chunks to multipart uploader."""
        proc = session.encoder_process
        if not proc or not proc.stdout:
            session.encoder_failed = True
            return

        try:
            while True:
                chunk = await proc.stdout.read(64 * 1024)
                if not chunk:
                    break
                await self._append_encoded_chunk(session, chunk)
        except Exception as e:
            session.encoder_failed = True
            logger.error(f"[Recording] Failed reading encoded stream for track={session.track_id}: {e}")

    async def _consume_encoder_stderr(self, session: MultipartRecordingSession) -> None:
        """Capture ffmpeg stderr for troubleshooting."""
        proc = session.encoder_process
        if not proc or not proc.stderr:
            return

        try:
            stderr_data = await proc.stderr.read()
            if stderr_data:
                session.encoder_stderr_tail = self._trim_text(
                    stderr_data.decode("utf-8", errors="replace")
                )
        except Exception as e:
            session.encoder_stderr_tail = self._trim_text(f"stderr-read-error: {e}")

    async def _start_encoder(self, session: MultipartRecordingSession) -> None:
        """Start ffmpeg process to encode PCM16 input to OGG/Opus."""
        ffmpeg_path = self.config.recording_upload.ffmpeg_path
        bitrate = self.config.recording_upload.opus_bitrate_kbps

        cmd = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "s16le",
            "-ar",
            "16000",
            "-ac",
            "1",
            "-i",
            "pipe:0",
            "-c:a",
            "libopus",
            "-b:a",
            f"{bitrate}k",
            "-vbr",
            "on",
            "-application",
            "voip",
            "-f",
            "ogg",
            "pipe:1",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if not proc.stdin or not proc.stdout or not proc.stderr:
            raise RuntimeError("ffmpeg process did not expose all expected pipes")

        session.encoder_process = proc
        session.encoder_stdout_task = asyncio.create_task(
            self._consume_encoder_stdout(session),
            name=f"encoder-stdout-{session.track_id}",
        )
        session.encoder_stderr_task = asyncio.create_task(
            self._consume_encoder_stderr(session),
            name=f"encoder-stderr-{session.track_id}",
        )

    async def _close_encoder_stdin(self, session: MultipartRecordingSession) -> None:
        """Signal end-of-input to ffmpeg encoder."""
        proc = session.encoder_process
        if not proc or not proc.stdin:
            return

        async with session.write_lock:
            if not proc.stdin.is_closing():
                proc.stdin.close()

    async def _wait_encoder_drain(self, session: MultipartRecordingSession) -> None:
        """Wait for encoder stdout/stderr drains and process termination."""
        proc = session.encoder_process
        if not proc:
            session.encoder_failed = True
            raise RuntimeError("encoder process missing")

        if session.encoder_stdout_task:
            await session.encoder_stdout_task
        if session.encoder_stderr_task:
            await session.encoder_stderr_task

        returncode = await proc.wait()
        if returncode != 0:
            session.encoder_failed = True
            raise RuntimeError(
                f"ffmpeg exited with code {returncode}; stderr={session.encoder_stderr_tail}"
            )

    async def _complete_upload_locked(self, session: MultipartRecordingSession) -> None:
        """Complete multipart upload. Caller must hold session.lock."""
        if not session.etags:
            raise RuntimeError(f"No uploaded parts for track={session.track_id}")

        client = self._get_s3_client()
        await asyncio.to_thread(
            client.complete_multipart_upload,
            Bucket=session.bucket,
            Key=session.object_key,
            UploadId=session.upload_id,
            MultipartUpload={"Parts": session.etags},
        )

    async def _abort_with_session(self, track_id: str, session: MultipartRecordingSession) -> None:
        """Abort multipart upload session safely."""
        await self._terminate_encoder(session)
        try:
            async with session.lock:
                client = self._get_s3_client()
                await asyncio.to_thread(
                    client.abort_multipart_upload,
                    Bucket=session.bucket,
                    Key=session.object_key,
                    UploadId=session.upload_id,
                )
            logger.warning(f"[Recording] Aborted multipart upload for track={track_id}")
        except Exception as e:
            if self._is_no_such_upload(e):
                logger.warning(
                    f"[Recording] Multipart already finalized for track={track_id} "
                    f"(NoSuchUpload on abort)"
                )
                return
            logger.error(f"[Recording] Abort failed for track={track_id}: {e}")

    async def _terminate_encoder(self, session: MultipartRecordingSession) -> None:
        """Terminate ffmpeg process and await cleanup tasks."""
        proc = session.encoder_process
        if not proc:
            return

        with contextlib.suppress(Exception):
            await self._close_encoder_stdin(session)

        if proc.returncode is None:
            proc.terminate()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=2.0)

        if session.encoder_stdout_task:
            with contextlib.suppress(Exception):
                await session.encoder_stdout_task
        if session.encoder_stderr_task:
            with contextlib.suppress(Exception):
                await session.encoder_stderr_task
