"""
Whisper Transcription Processor

Downloads audio from MinIO to temp directory → Whisper transcription → Cleanup.
Uses traditional download approach for better reliability and resource management.
"""

import asyncio
import logging
import os
import tempfile
import shutil
import math
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass

from minio import Minio
from faster_whisper import WhisperModel

from ..config import get_config
from .transcription_queue_service import TranscriptionTask

from datetime import datetime, timedelta, timezone


logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """A segment of transcribed text with timestamps."""
    start: float
    end: float
    text: str
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
        }


class WhisperTranscriptionProcessor:
    """
    Processor that transcribes audio files from MinIO using Whisper.
    
    Flow: MinIO → Download to temp → Whisper → Cleanup
    """
    
    _instance: Optional['WhisperTranscriptionProcessor'] = None
    
    def __init__(self):
        self._config = get_config()
        self._minio_client: Optional[Minio] = None
        self._whisper_model: Optional[WhisperModel] = None
        self._initialized = False
        self._temp_dir: Optional[Path] = None
        
        logger.info("WhisperTranscriptionProcessor created (download-based)")
    
    @classmethod
    def get_instance(cls) -> 'WhisperTranscriptionProcessor':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def initialize(self):
        """Initialize MinIO client, Whisper model, and temp directory."""
        if self._initialized:
            return
        
        logger.info("Initializing WhisperTranscriptionProcessor...")
        
        # Create temp directory for audio files
        self._temp_dir = Path(tempfile.gettempdir()) / "whisper_transcriptions"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"📁 Temp directory: {self._temp_dir}")
        
        # Initialize MinIO client
        minio_config = self._config.minio
        self._minio_client = Minio(
            minio_config.endpoint,
            access_key=minio_config.access_key,
            secret_key=minio_config.secret_key,
            secure=minio_config.secure,
        )
        logger.info(f"✅ MinIO client connected to {minio_config.endpoint}")
        
        # Verify bucket exists
        try:
            if not self._minio_client.bucket_exists(minio_config.bucket):
                raise RuntimeError(f"Bucket '{minio_config.bucket}' does not exist")
        except Exception as e:
            logger.error(f"Failed to verify MinIO bucket: {e}")
            raise
        
        # Initialize Whisper model (this can take time, especially for large models)
        whisper_config = self._config.whisper
        logger.info(f"Loading Whisper model '{whisper_config.model_size}' on {whisper_config.device}...")
        
        # Run model loading in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        self._whisper_model = await loop.run_in_executor(
            None,
            lambda: WhisperModel(
                whisper_config.model_size,
                device=whisper_config.device,
                compute_type=whisper_config.compute_type,
            )
        )
        logger.info(f"✅ Whisper model loaded: {whisper_config.model_size}")
        
        self._initialized = True
    
    async def _download_from_minio(self, filename: str) -> Path:
        """
        Download audio file from MinIO to temp directory.
        
        Args:
            filename: Path to file in MinIO bucket
            
        Returns:
            Path to downloaded file
            
        Raises:
            RuntimeError: If download fails
        """
        if not self._minio_client:
            raise RuntimeError("MinIO client not initialized")
        
        if not self._temp_dir:
            raise RuntimeError("Temp directory not initialized")
        
        # Validate filename
        if not filename or filename.strip() == "":
            raise ValueError("Filename cannot be empty")
        
        # Create safe local filename (preserve extension)
        safe_filename = Path(filename).name
        local_path = self._temp_dir / safe_filename
        
        # Check if file exists in MinIO
        try:
            stat = self._minio_client.stat_object(
                self._config.minio.bucket,
                filename
            )
            file_size_mb = stat.size / (1024 * 1024)
            logger.info(f"📦 File size: {file_size_mb:.2f} MB")
        except Exception as e:
            logger.error(f"File not found in MinIO: {filename}")
            raise RuntimeError(f"File not found in MinIO: {filename}") from e
        
        # Download file
        logger.info(f"⬇️  Downloading {filename} to {local_path}...")
        
        try:
            # Run download in executor to avoid blocking
            loop = asyncio.get_event_loop()
            
            def do_download():
                self._minio_client.fget_object(
                    self._config.minio.bucket,
                    filename,
                    str(local_path)
                )
            
            await loop.run_in_executor(None, do_download)
            
            # Verify download
            if not local_path.exists():
                raise RuntimeError(f"Download failed: {local_path} does not exist")
            
            downloaded_size_mb = local_path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Downloaded {downloaded_size_mb:.2f} MB to {local_path}")
            
            return local_path
            
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            # Cleanup partial download
            if local_path.exists():
                local_path.unlink()
            raise RuntimeError(f"Failed to download {filename}") from e
    
    async def _transcribe_audio_file(self, audio_path: Path) -> tuple[str, List[TranscriptionSegment], Dict[str, Any]]:
        """
        Transcribe audio file using Whisper (standard faster-whisper approach).
        
        Args:
            audio_path: Path to audio file on disk
            
        Returns:
            Tuple of (full_text, segments, info)
        """
        if not self._whisper_model:
            raise RuntimeError("Whisper model not initialized")
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        whisper_config = self._config.whisper
        
        logger.info(f"🎤 Transcribing {audio_path.name}...")
        
        # Run transcription in thread pool (Whisper is CPU/GPU intensive)
        loop = asyncio.get_event_loop()
        
        def run_transcription():
            # Standard faster-whisper usage: pass file path directly
            segments, info = self._whisper_model.transcribe(
                str(audio_path),  # Pass file path as string
                language=whisper_config.language if whisper_config.language else None,
                beam_size=whisper_config.beam_size,
                vad_filter=whisper_config.vad_filter,
            )
            return list(segments), info
        
        segments_raw, info = await loop.run_in_executor(None, run_transcription)
        
        # Convert to our segment format
        segments = [
            TranscriptionSegment(
                start=seg.start,
                end=seg.end,
                text=seg.text.strip(),
            )
            for seg in segments_raw
        ]
        
        # Combine all text
        full_text = " ".join(seg.text for seg in segments)
        
        # Handle NaN in language_probability
        lang_prob = info.language_probability
        if math.isnan(lang_prob):
            lang_prob = 0.0
        
        info_dict = {
            "language": info.language,
            "language_probability": lang_prob,
            "duration": info.duration,
            "duration_after_vad": info.duration_after_vad,
        }
        
        return full_text, segments, info_dict
    
    def _cleanup_temp_file(self, file_path: Path):
        """
        Remove temporary audio file.
        
        Args:
            file_path: Path to file to remove
        """
        try:
            if file_path.exists():
                file_path.unlink()
                logger.debug(f"🗑️  Cleaned up temp file: {file_path.name}")
        except Exception as e:
            logger.warning(f"Failed to cleanup temp file {file_path}: {e}")
    
    async def process(self, task: TranscriptionTask) -> str:
        """
        Process a transcription task.
        
        This is the main entry point called by TranscriptionQueueService.
        
        Args:
            task: TranscriptionTask with file information
            
        Returns:
            Transcribed text
        """
        # Ensure initialized
        await self.initialize()
        
        logger.info(f"🎯 Processing transcription for: {task.filename}")
        
        local_file: Optional[Path] = None
        
        try:
            # Step 1: Download from MinIO
            local_file = await self._download_from_minio(task.filename)
            
            # Step 2: Transcribe
            full_text, segments, info = await self._transcribe_audio_file(local_file)
            
            # Step 3: Log results
            lang_prob = info['language_probability']
            lang_prob_str = f"{lang_prob:.1%}" if not math.isnan(lang_prob) else "N/A"
            
            logger.info(
                f"📝 Transcription complete for {task.filename}:\n"
                f"   Language: {info['language']} ({lang_prob_str})\n"
                f"   Duration: {info['duration']:.2f}s\n"
                f"   Duration after VAD: {info['duration_after_vad']:.2f}s\n"
                f"   Segments: {len(segments)}\n"
                f"   Text length: {len(full_text)} chars"
            )

            # task.started_at = 1768550217356673262 (ns)
            started_at_ns = int(task.started_at)

            # Convert nanoseconds → seconds
            started_at_dt = datetime.fromtimestamp(
                started_at_ns / 1_000_000_000,
                tz=timezone.utc
            )

            for seg in segments:
                start_time = started_at_dt + timedelta(seconds=seg.start)
                end_time = started_at_dt + timedelta(seconds=seg.end)

                print(
                    "[{} → {}] {}".format(
                        start_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        end_time.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                        seg.text
                    )
                )
            return full_text
            
        except Exception as e:
            logger.error(f"Failed to process transcription for {task.filename}: {e}")
            raise
            
        finally:
            # Step 4: Always cleanup temp file
            if local_file:
                self._cleanup_temp_file(local_file)
    
    async def shutdown(self):
        """Cleanup resources and temp directory."""
        logger.info("Shutting down WhisperTranscriptionProcessor")
        
        # Cleanup temp directory
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
                logger.info(f"🗑️  Removed temp directory: {self._temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to remove temp directory: {e}")
        
        # Reset state
        self._whisper_model = None
        self._minio_client = None
        self._initialized = False


def get_whisper_processor() -> WhisperTranscriptionProcessor:
    """Get the singleton Whisper processor instance."""
    return WhisperTranscriptionProcessor.get_instance()


async def transcribe_task(task: TranscriptionTask) -> str:
    """
    Convenience function to transcribe a task.
    
    Can be used directly as the processor for TranscriptionQueueService:
        queue_service.set_processor(transcribe_task)
    """
    processor = get_whisper_processor()
    return await processor.process(task)