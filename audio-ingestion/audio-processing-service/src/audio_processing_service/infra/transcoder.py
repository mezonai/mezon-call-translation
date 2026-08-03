"""Raw PCM -> OGG/Opus transcode via ffmpeg subprocess.

Command line recovered from the deleted agent-side encoder
(agents/src/services/audio_recording_manager.py, see git history) -- kept
identical on purpose so the output format matches exactly what client/bot
already integrate against (audio-ingestion PLAN.md D20: keep the existing
format). File-based rather than piped (see infra/storage.py's docstring for
why) -- input/output are plain file paths instead of pipe:0/pipe:1.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from audio_processing_service.config import TranscodeConfig


class TranscodeError(Exception):
    pass


async def transcode_pcm_to_ogg(
    input_path: Path, output_path: Path, config: TranscodeConfig
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [
        config.ffmpeg_path,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "s16le",
        "-ar", str(config.sample_rate),
        "-ac", str(config.channels),
        "-i", str(input_path),
        "-c:a", "libopus",
        "-b:a", f"{config.opus_bitrate_kbps}k",
        "-vbr", "on",
        "-application", "voip",
        "-f", "ogg",
        str(output_path),
    ]

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        _, stderr = await asyncio.wait_for(
            process.communicate(), timeout=config.ffmpeg_timeout_seconds
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise TranscodeError(
            f"ffmpeg timed out after {config.ffmpeg_timeout_seconds}s: {input_path.name}"
        )

    if process.returncode != 0:
        raise TranscodeError(
            f"ffmpeg exited {process.returncode} for {input_path.name}: "
            f"{stderr.decode(errors='replace')[:1000]}"
        )

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise TranscodeError(f"ffmpeg produced no output for {input_path.name}")
