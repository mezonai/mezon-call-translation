import logging
import os
import subprocess
import tempfile
import uuid
from typing import Optional

import httpx
import soundfile as sf

logger = logging.getLogger(__name__)


def convert_audio_to_wav(input_path: str, sr: int = 16000) -> str:
    """
    Convert an input audio/video file to standard 16kHz mono WAV format via FFmpeg.

    Args:
        input_path: Path to the input file (supports mp3, mp4, m4a, wav, ogg, etc.).
        sr: Target sample rate in Hz (default: 16000).

    Returns:
        Absolute path to the temporary normalized WAV file.

    Raises:
        FileNotFoundError: If input_path does not exist.
        RuntimeError: If FFmpeg conversion fails.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"Input audio file not found: {input_path}")

    temp_dir = tempfile.gettempdir()
    unique_filename = f"stt_parakeet_{uuid.uuid4().hex}.wav"
    output_path = os.path.join(temp_dir, unique_filename)

    command = [
        "ffmpeg",
        "-y",
        "-i", input_path,
        "-ar", str(sr),
        "-ac", "1",
        output_path,
    ]

    try:
        subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg audio conversion failed for '%s': %s", input_path, e)
        raise RuntimeError(f"FFmpeg audio conversion failed: {e}")


async def download_url_to_temp(url: str, timeout: float = 60.0) -> str:
    """
    Download audio content from a remote HTTP/HTTPS URL into a local temporary file.

    Args:
        url: Remote HTTP/HTTPS audio URL.
        timeout: Request timeout in seconds.

    Returns:
        Absolute path to the downloaded temporary file.

    Raises:
        RuntimeError: If downloading fails or network error occurs.
    """
    temp_dir = tempfile.gettempdir()
    parsed_ext = os.path.splitext(url.split("?")[0])[1] or ".audio"
    temp_filename = f"stt_dl_{uuid.uuid4().hex}{parsed_ext}"
    temp_path = os.path.join(temp_dir, temp_filename)

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                with open(temp_path, "wb") as f:
                    async for chunk in response.aiter_bytes(chunk_size=65536):
                        f.write(chunk)
        return temp_path
    except Exception as e:
        cleanup_temp_files(temp_path)
        logger.error("Failed to download audio from '%s': %s", url, e)
        raise RuntimeError(f"Failed to download audio from URL: {e}")


def save_bytes_to_temp(audio_bytes: bytes, filename: str = "upload.bin") -> str:
    """
    Save raw in-memory audio bytes to a temporary file on disk.

    Args:
        audio_bytes: Binary audio content.
        filename: Original filename to preserve file extension hint.

    Returns:
        Absolute path to the created temporary file.
    """
    temp_dir = tempfile.gettempdir()
    ext = os.path.splitext(filename)[1] or ".bin"
    temp_filename = f"stt_raw_{uuid.uuid4().hex}{ext}"
    temp_path = os.path.join(temp_dir, temp_filename)

    with open(temp_path, "wb") as f:
        f.write(audio_bytes)

    return temp_path


def get_audio_duration(wav_path: str) -> float:
    """
    Get audio duration in seconds using soundfile.

    Args:
        wav_path: Path to the WAV audio file.

    Returns:
        Duration as a float in seconds.
    """
    info = sf.info(wav_path)
    return float(info.duration)


def cleanup_temp_files(*paths: Optional[str]) -> None:
    """
    Silently delete temporary files if they exist on the filesystem.

    Args:
        paths: Arbitrary number of file path strings to delete.
    """
    for p in paths:
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except OSError:
                pass
