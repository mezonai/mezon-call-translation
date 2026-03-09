"""
Whisper Transcription Processor - BATCHED REDIS MODE

Transcription flow with Redis-based batched sending:
1. Download audio from MinIO
2. Transcribe and collect segments in memory
3. Send batches of segments as Redis tasks (200 segments per task)
4. Orchestrator will consume tasks and save to MongoDB progressively
"""

import asyncio
import logging
import tempfile
import shutil
import math
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass

from minio import Minio
from faster_whisper import WhisperModel

from stt_service.service.redis.redis_producer_service import RedisProducerService
from stt_service.utils.decorator import singleton

from ..config import get_config
from ..models import TranscriptionStreamTask, SaveTranscriptionTask

logger = logging.getLogger(__name__)


@dataclass
class TranscriptionSegment:
    """A segment of transcribed text with timestamps."""
    start: float
    end: float
    text: str
    confidence: float

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "confidence": self.confidence,
        }


@singleton
class WhisperTranscriptionProcessor:
    """
    Processor that transcribes audio files using Whisper and sends batches to Redis.
    
    Flow:
    1. Download audio from MinIO
    2. Transcribe audio and collect all segments in memory
    3. Split segments into batches (200 per batch)
    4. Send each batch as SaveTranscriptionTask to Redis
    5. Orchestrator consumer will save batches to MongoDB
    6. Cleanup temp files
    """
    
    # Configuration
    CHUNK_BATCH_SIZE = 50  # Send to Redis every 200 segments
    SAVE_STREAM_KEY = "save_transcription:stream"  # Redis stream for save tasks
    
    def __init__(self):
        self._config = get_config()
        self._minio_client: Optional[Minio] = None
        self._whisper_model: Optional[WhisperModel] = None
        self._initialized = False
        self._temp_dir: Optional[Path] = None
        self._redis_producer: Optional[RedisProducerService] = None
        self.CHUNK_BATCH_SIZE = self._config.Transcirpt.chunk_size
        logger.info("WhisperTranscriptionProcessor created (batched Redis mode)")
    
    async def initialize(self):
        """Initialize MinIO client, Whisper model, Redis producer, and temp directory."""
        if self._initialized:
            return
        
        logger.info("Initializing WhisperTranscriptionProcessor...")
        
        # Initialize Redis producer for save tasks
        self._redis_producer = RedisProducerService.get_instance(
            task_class=SaveTranscriptionTask,
            stream_key=self.SAVE_STREAM_KEY
        )
        await self._redis_producer.connect()
        logger.info("✅ Redis producer connected")
        
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
        
        # Initialize Whisper model
        whisper_config = self._config.whisper
        logger.info(
            f"Loading Whisper model '{whisper_config.model_size}' "
            f"on {whisper_config.device}..."
        )
        
        loop = asyncio.get_event_loop()
        self._whisper_model = await loop.run_in_executor(
            None,
            lambda: WhisperModel(
                whisper_config.model_size,
                device=whisper_config.device,
                compute_type=whisper_config.compute_type,
                cpu_threads=whisper_config.cpu_threads,
            )
        )
        logger.info(f"✅ Whisper model loaded: {whisper_config.model_size}")
        
        self._initialized = True
    
    async def _download_from_minio(self, filename: str) -> tuple[Path, float]:
        """
        Download audio file from MinIO to temp directory.
        
        Args:
            filename: Path to file in MinIO bucket
            
        Returns:
            Tuple of (local_path, file_size_mb)
            
        Raises:
            RuntimeError: If download fails
        """
        if not self._minio_client:
            raise RuntimeError("MinIO client not initialized")
        
        if not self._temp_dir:
            raise RuntimeError("Temp directory not initialized")
        
        if not filename or filename.strip() == "":
            raise ValueError("Filename cannot be empty")
        
        # Create safe local filename
        safe_filename = Path(filename).name
        local_path = self._temp_dir / safe_filename
        
        # Check if file exists in MinIO and get size
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
            loop = asyncio.get_event_loop()
            
            def do_download():
                self._minio_client.fget_object(
                    self._config.minio.bucket,
                    filename,
                    str(local_path)
                )
            
            await loop.run_in_executor(None, do_download)
            
            if not local_path.exists():
                raise RuntimeError(f"Download failed: {local_path} does not exist")
            
            downloaded_size_mb = local_path.stat().st_size / (1024 * 1024)
            logger.info(f"✅ Downloaded {downloaded_size_mb:.2f} MB to {local_path}")
            
            return local_path, file_size_mb
            
        except Exception as e:
            logger.error(f"Failed to download {filename}: {e}")
            if local_path.exists():
                local_path.unlink()
            raise RuntimeError(f"Failed to download {filename}") from e
    
    async def _transcribe_and_collect(
        self,
        audio_path: Path,
    ) -> tuple[List[Dict], str]:
        """
        Transcribe audio and collect all segments in memory.
        
        Args:
            audio_path: Path to audio file
            
        Returns:
            Tuple of (segments_list, full_text)
        """
        if not self._whisper_model:
            raise RuntimeError("Whisper model not initialized")
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        whisper_config = self._config.whisper
        logger.info(f"🎤 Starting transcription for {audio_path.name}...")
        
        # Run transcription in thread pool
        loop = asyncio.get_event_loop()
        
        def transcribe_in_thread():
            """Transcribe audio and collect all segments"""
            try:
                segments_generator, info = self._whisper_model.transcribe(
                    str(audio_path),
                    language=whisper_config.language if whisper_config.language else None,
                    beam_size=whisper_config.beam_size,
                    vad_filter=whisper_config.vad_filter,
                )
                
                # Collect all segments
                segments = []
                full_text_parts = []
                
                for seg in segments_generator:
                    segment = TranscriptionSegment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text.strip(),
                        confidence=round(math.exp(seg.avg_logprob), 4)
                    )
                    
                    segment_dict = segment.to_dict()
                    segments.append(segment_dict)
                    full_text_parts.append(segment_dict['text'])
                
                full_text = " ".join(full_text_parts)
                
                return segments, full_text
                
            except Exception as e:
                logger.error(f"❌ Transcription failed in thread: {e}", exc_info=True)
                raise RuntimeError(f"Whisper transcription failed: {e}") from e
        
        # Run in thread pool
        segments, full_text = await loop.run_in_executor(
            None,
            transcribe_in_thread
        )
        
        logger.info(
            f"✅ Transcription complete: {len(segments)} segments, "
            f"{len(full_text)} characters"
        )
        
        return segments, full_text
    
    async def _send_batches_to_redis(
        self,
        track_ref_id: str,
        segments: List[Dict],
    ) -> int:
        """
        Split segments into batches and send to Redis as SaveTranscriptionTask.
        
        Args:
            track_ref_id: Egress ID / Track reference
            segments: All transcription segments
            
        Returns:
            Number of batches sent
        """
        if not self._redis_producer:
            raise RuntimeError("Redis producer not initialized")
        
        total_segments = len(segments)
        num_batches = math.ceil(total_segments / self.CHUNK_BATCH_SIZE)
        
        logger.info(
            f"📤 Sending {total_segments} segments in {num_batches} batches "
            f"({self.CHUNK_BATCH_SIZE} per batch)"
        )
        
        for chunk_idx in range(num_batches):
            # Calculate batch range
            start_idx = chunk_idx * self.CHUNK_BATCH_SIZE
            end_idx = min(start_idx + self.CHUNK_BATCH_SIZE, total_segments)
            batch_segments = segments[start_idx:end_idx]
            
            # Get time range
            start_time = batch_segments[0]['start']
            end_time = batch_segments[-1]['end']
            
            # Check if this is the final batch
            is_final = (chunk_idx == num_batches - 1)
            
            # Create task
            task = SaveTranscriptionTask(
                track_ref_id=track_ref_id,
                segments=batch_segments,
                chunk_index=chunk_idx,
                start_time=start_time,
                end_time=end_time,
                item_count=len(batch_segments),
                is_final=is_final,
                status="pending",
            )
            
            # Send to Redis
            task_id = await self._redis_producer.enqueue(task)
            
            logger.info(
                f"📥 Sent batch {chunk_idx + 1}/{num_batches}: "
                f"{len(batch_segments)} segments, "
                f"time={start_time:.1f}-{end_time:.1f}s, "
                f"final={is_final}, task_id={task_id}"
            )
        
        logger.info(f"✅ All {num_batches} batches sent to Redis")
        return num_batches
            
    
    
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
    
    async def process(self, task: TranscriptionStreamTask) -> str:
        """
        Process a transcription task by sending batches to Redis.
        
        Main entry point that orchestrates the entire transcription flow:
        1. Initialize resources
        2. Download audio from MinIO
        3. Transcribe and collect segments in memory
        4. Split into batches and send to Redis
        5. Orchestrator consumer will save to MongoDB progressively
        6. Cleanup temp files
        
        Args:
            task: TranscriptionStreamTask with file information
            
        Returns:
            Full transcribed text
            
        Raises:
            RuntimeError: If any step fails
        """
        await self.initialize()
        
        logger.info(f"🎯 Processing transcription task: {task.filename}")
        logger.info(f"   Egress: {task.egress_id}\n")
        
        local_file: Optional[Path] = None
        track_ref_id = task.egress_id
        
        try:
            # STEP 1: Download audio from MinIO
            local_file, file_size_mb = await self._download_from_minio(task.filename)
            
            # STEP 2: Transcribe and collect all segments
            segments, full_text = await self._transcribe_and_collect(local_file)
            
            # STEP 3: Send batches to Redis
            num_batches = await self._send_batches_to_redis(
                track_ref_id=track_ref_id,
                segments=segments,
            )
            
            # STEP 4: Log final results
            logger.info(
                f"\n{'='*60}\n"
                f"✅ TRANSCRIPTION COMPLETE - BATCHES SENT TO REDIS\n"
                f"{'='*60}\n"
                f"File: {task.filename}\n"
                f"Size: {file_size_mb:.2f} MB\n"
                f"Total segments: {len(segments)}\n"
                f"Batches sent: {num_batches}\n"
                f"Text length: {len(full_text)} characters\n"
                f"Track ID: {track_ref_id}\n"
                f"Status: Pending save in Redis queue\n"
                f"{'='*60}"
            )
            
            return full_text
            
        except Exception as e:
            logger.error(f"❌ Failed to process transcription: {e}")
            raise
            
        finally:
            # STEP 5: Always cleanup temp file
            if local_file:
                self._cleanup_temp_file(local_file)
    
    async def shutdown(self):
        """
        Cleanup resources and temp directory.
        
        Should be called when shutting down the service.
        """
        logger.info("Shutting down WhisperTranscriptionProcessor...")
        
        # Cleanup temp directory
        if self._temp_dir and self._temp_dir.exists():
            try:
                shutil.rmtree(self._temp_dir)
                logger.info(f"🗑️  Removed temp directory: {self._temp_dir}")
            except Exception as e:
                logger.warning(f"Failed to remove temp directory: {e}")
        
        # Close Redis producer
        if self._redis_producer:
            await self._redis_producer.close()
        
        # Reset state
        self._whisper_model = None
        self._minio_client = None
        self._redis_producer = None
        self._initialized = False
        
        logger.info("✅ WhisperTranscriptionProcessor shutdown complete")


# ============================================================
# PUBLIC API
# ============================================================



async def transcribe_task(task: TranscriptionStreamTask) -> str:
    """
    Convenience function to transcribe a task.
    
    Can be used directly as the processor for TranscriptionQueueService:
        queue_service.set_processor(transcribe_task)
    
    Args:
        task: TranscriptionStreamTask to process
        
    Returns:
        Full transcribed text
    """
    processor = WhisperTranscriptionProcessor()
    return await processor.process(task)