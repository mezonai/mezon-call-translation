"""ffmpeg-subprocess StreamEncoder: PCM16 in, OGG/Opus out, streamed through
pipes instead of files (PLAN.md D6-successor).

Same ffmpeg args as the now-retired audio-processing-service's
infra/transcoder.py (kept identical on purpose -- output format must still
match what client/bot already integrate against, PLAN.md D20), just
`pipe:0`/`pipe:1` instead of file paths and run for the life of a session
instead of once at the end.

One subprocess per session -- if one session's ffmpeg dies, it can't take
any other session down with it (unlike a shared in-process encoder), and
AppendAudio (application/append_audio.py) is the one deciding what a death
means for the session (annotate + restart), not this adapter.
"""

from __future__ import annotations

import asyncio
import logging

from record_service.config import TranscodeConfig
from record_service.domain.ports import StreamEncoder, StreamEncoderFactory

logger = logging.getLogger(__name__)

_READ_CHUNK_SIZE = 65536


class FfmpegOpusEncoder(StreamEncoder):
    def __init__(self, config: TranscodeConfig, sample_rate: int, channels: int) -> None:
        self._config = config
        self._sample_rate = sample_rate
        self._channels = channels
        self._process: asyncio.subprocess.Process | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._out_chunks: list[bytes] = []
        self._out_lock = asyncio.Lock()
        self._died = False

    async def start(self) -> None:
        args = [
            self._config.ffmpeg_path,
            "-hide_banner",
            "-loglevel", "error",
            "-f", "s16le",
            "-ar", str(self._sample_rate),
            "-ac", str(self._channels),
            "-i", "pipe:0",
            "-c:a", "libopus",
            "-b:a", f"{self._config.opus_bitrate_kbps}k",
            "-vbr", "on",
            "-application", "voip",
            "-f", "ogg",
            "pipe:1",
        ]
        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self) -> None:
        assert self._process is not None and self._process.stdout is not None
        try:
            while True:
                chunk = await self._process.stdout.read(_READ_CHUNK_SIZE)
                if not chunk:
                    break
                async with self._out_lock:
                    self._out_chunks.append(chunk)
        except Exception:  # noqa: BLE001 - a dead reader must still mark the encoder dead
            logger.exception("ffmpeg stdout reader crashed")
        finally:
            self._died = True

    async def feed(self, pcm: bytes) -> None:
        if self._process is None or self._process.stdin is None or self._process.returncode is not None:
            self._died = True
            raise RuntimeError("encoder process is not running")
        try:
            self._process.stdin.write(pcm)
            await self._process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            self._died = True
            raise RuntimeError("encoder stdin pipe broken") from exc

    async def drain(self) -> bytes:
        async with self._out_lock:
            if not self._out_chunks:
                return b""
            data = b"".join(self._out_chunks)
            self._out_chunks.clear()
            return data

    async def close(self) -> bytes:
        if self._process is not None and self._process.stdin is not None:
            try:
                self._process.stdin.close()
            except Exception:  # noqa: BLE001 - best-effort, we still wait+drain below
                pass
        if self._reader_task is not None:
            try:
                await asyncio.wait_for(self._reader_task, timeout=self._config.ffmpeg_timeout_seconds)
            except asyncio.TimeoutError:
                logger.error("ffmpeg did not flush within %.0fs, killing it", self._config.ffmpeg_timeout_seconds)
                if self._process is not None:
                    self._process.kill()
        if self._process is not None:
            await self._process.wait()
        return await self.drain()

    @property
    def alive(self) -> bool:
        return self._process is not None and self._process.returncode is None and not self._died


class FfmpegOpusEncoderFactory(StreamEncoderFactory):
    def __init__(self, config: TranscodeConfig) -> None:
        self._config = config

    async def create(self, sample_rate: int, channels: int) -> StreamEncoder:
        encoder = FfmpegOpusEncoder(self._config, sample_rate, channels)
        await encoder.start()
        return encoder
