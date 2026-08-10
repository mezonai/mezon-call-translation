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
from typing import Optional, Dict, Any
from pathlib import Path
from dataclasses import dataclass

import numpy as np
from minio import Minio
from faster_whisper import WhisperModel

from stt_service.service.redis.redis_producer_service import RedisProducerService
from stt_service.utils.decorator import singleton

from stt_service.config import get_config
from stt_service.models import TranscriptionStreamTask, SaveTranscriptionTask

logger = logging.getLogger(__name__)


def _pcm16_bytes_to_float32(raw_bytes: bytes) -> np.ndarray:
    """
    Converts a raw, headerless PCM16 mono buffer (audio-ingestion PLAN.md D6
    -- record-service's capture format) directly into the normalized float32
    array faster_whisper's WhisperModel.transcribe() accepts as `audio`.

    Bypasses transcribe()'s default path entirely: when `audio` is anything
    other than a np.ndarray, it calls decode_audio() (faster_whisper/audio.py),
    which shells out to PyAV to auto-detect the input's container/format --
    that fails hard on headerless raw PCM (av.error.InvalidDataError:
    "Invalid data found when processing input"), since there's no header for
    it to sniff. Passing a np.ndarray skips that branch
    (`if not isinstance(audio, np.ndarray): audio = decode_audio(...)`).

    Does the exact same int16 -> float32 conversion decode_audio() itself
    does internally (`astype(np.float32) / 32768.0`) so the numeric range
    Whisper's feature extractor sees is identical either way.
    """
    # Defensive: drop a stray trailing byte rather than let np.frombuffer
    # raise on a non-multiple-of-2 buffer (shouldn't happen in practice).
    if len(raw_bytes) % 2:
        raw_bytes = raw_bytes[:-1]
    samples = np.frombuffer(raw_bytes, dtype=np.int16)
    return samples.astype(np.float32) / 32768.0


@dataclass
class TranscriptionSegment:
    """A segment of transcribed text with timestamps."""
    start: float
    end: float
    text: str
    confidence: float
    metadata: Dict[str, Any] = None

    def to_dict(self) -> dict:
        return {
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "confidence": self.confidence,
            "metadata": self.metadata,
        }


@singleton
class WhisperTranscriptionProcessor:
    """
    Processor that transcribes audio files using Whisper and streams batches to Redis.
    
    Flow:
    1. Download audio from MinIO
    2. Transcribe audio and stream batches to Redis as segments are collected
    3. Send batch immediately when CHUNK_BATCH_SIZE segments are ready
    4. On success: send final "completed" marker task
    5. On error: send "failed" marker task to notify consumer
    6. Orchestrator consumer saves batches to MongoDB and updates track status
    7. Cleanup temp files
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

        # We now feed raw PCM16 straight in as a pre-decoded np.ndarray
        # (see _pcm16_bytes_to_float32), skipping decode_audio()'s own
        # resampling step entirely -- so the capture format MUST already
        # match what the model's feature extractor expects, or every
        # transcription would silently run on mis-rated audio (sped
        # up/slowed down) instead of loudly failing like before. Raw
        # capture is always mono (audio-ingestion PLAN.md D6); fail fast at
        # startup rather than per-task if that ever stops being true.
        audio_config = self._config.audio
        extractor_rate = self._whisper_model.feature_extractor.sampling_rate
        if audio_config.sample_rate != extractor_rate:
            raise RuntimeError(
                f"Capture sample_rate ({audio_config.sample_rate}) does not match "
                f"Whisper's expected sampling_rate ({extractor_rate}) -- raw PCM is fed "
                f"in directly without resampling, see _pcm16_bytes_to_float32."
            )
        if audio_config.channels != 1:
            raise RuntimeError(
                f"Capture channels ({audio_config.channels}) != 1 -- raw PCM is assumed "
                f"mono and fed in directly without deinterleaving, see _pcm16_bytes_to_float32."
            )

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
    
    async def _transcribe_and_stream_batches(
        self,
        audio_path: Path,
        track_ref_id: str,
    ) -> int:
        """
        Transcribe audio and stream batches to Redis as they're collected.
        Sends batch immediately when CHUNK_BATCH_SIZE is reached.
        
        Args:
            audio_path: Path to audio file
            track_ref_id: Egress ID / Track reference
            
        Returns:
            Number of batches sent
        """
        if not self._whisper_model:
            raise RuntimeError("Whisper model not initialized")
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        if not self._redis_producer:
            raise RuntimeError("Redis producer not initialized")
        
        whisper_config = self._config.whisper
        logger.info(f"🎤 Starting transcription for {audio_path.name}...")
        
        # Create queue for communication between thread and async code
        batch_queue = asyncio.Queue()
        
        # Run transcription in thread pool
        loop = asyncio.get_event_loop()
        
        def transcribe_in_thread():
            """Transcribe audio and send batches via queue"""
            try:
                audio_array = _pcm16_bytes_to_float32(audio_path.read_bytes())
                segments_generator, info = self._whisper_model.transcribe(
                    audio_array,
                    language=whisper_config.language if whisper_config.language else None,
                    beam_size=whisper_config.beam_size,
                    vad_filter=whisper_config.vad_filter,
                )
                
                # Collect segments into batches
                current_batch = []
                
                for seg in segments_generator:
                    segment = TranscriptionSegment(
                        start=seg.start,
                        end=seg.end,
                        text=seg.text.strip(),
                        confidence=round(math.exp(seg.avg_logprob), 4),
                        metadata={
                            "avg_logprob": seg.avg_logprob,
                            "compression_ratio": seg.compression_ratio,
                            "no_speech_prob": seg.no_speech_prob,
                            "words": seg.words,
                            "temperature": seg.temperature,
                        }
                    )
                    
                    segment_dict = segment.to_dict()
                    current_batch.append(segment_dict)
                    
                    # When batch is full, send it immediately
                    if len(current_batch) >= self.CHUNK_BATCH_SIZE:
                        asyncio.run_coroutine_threadsafe(
                            batch_queue.put(('batch', current_batch.copy())),
                            loop
                        )
                        current_batch.clear()
                
                # Send remaining segments as final batch
                if current_batch:
                    asyncio.run_coroutine_threadsafe(
                        batch_queue.put(('batch', current_batch)),
                        loop
                    )
                
                # Signal completion
                asyncio.run_coroutine_threadsafe(
                    batch_queue.put((
                        'done',
                        {
                            "duration_after_vad_sec": float(info.duration_after_vad)
                        }
                    )),
                    loop
                )
                
            except Exception as e:
                logger.error(f"❌ Transcription failed in thread: {e}", exc_info=True)
                # Signal error
                asyncio.run_coroutine_threadsafe(
                    batch_queue.put(('error', str(e))),
                    loop
                )
        
        # Start transcription in thread
        transcription_task = loop.run_in_executor(None, transcribe_in_thread)
        
        # Process batches as they arrive
        chunk_index = 0
        total_segments = 0
        
        try:
            while True:
                msg_type, data = await batch_queue.get()
                
                if msg_type == 'batch':
                    batch_segments = data
                    total_segments += len(batch_segments)
                    
                    start_time = batch_segments[0]['start']
                    end_time = batch_segments[-1]['end']
                    
                    task = SaveTranscriptionTask(
                        track_ref_id=track_ref_id,
                        segments=batch_segments,
                        chunk_index=chunk_index,
                        start_time=start_time,
                        end_time=end_time,
                        item_count=len(batch_segments),
                        is_final=False,
                        status="pending",
                    )
                    
                    await self._redis_producer.enqueue(task)
                    
                    logger.info(
                        f"📥 Sent batch {chunk_index + 1}: "
                        f"{len(batch_segments)} segments, "
                        f"time={start_time:.1f}-{end_time:.1f}s"
                    )
                    
                    chunk_index += 1
                    
                elif msg_type == 'done':
                    # Send final marker task
                    final_task = SaveTranscriptionTask(
                        track_ref_id=track_ref_id,
                        segments=[],
                        chunk_index=chunk_index,
                        start_time=0.0,
                        end_time=0.0,
                        item_count=0,
                        is_final=True,
                        status="completed",
                        duration_after_vad_sec=data["duration_after_vad_sec"],
                    )
                    await self._redis_producer.enqueue(final_task)
                    
                    logger.info(
                        f"✅ Transcription complete: {total_segments} segments, "
                        f"{chunk_index} batches sent"
                    )
                    break
                    
                elif msg_type == 'error':
                    error_msg = data
                    
                    # Send failed task to notify consumer
                    failed_task = SaveTranscriptionTask(
                        track_ref_id=track_ref_id,
                        segments=[],
                        chunk_index=chunk_index,
                        start_time=0.0,
                        end_time=0.0,
                        item_count=0,
                        is_final=True,
                        status="failed",
                    )
                    await self._redis_producer.enqueue(failed_task)
                    logger.info("📤 Sent failed task to Redis for consumer to mark track as failed")
                    
                    # Wait for thread to complete
                    await transcription_task
                    
                    raise RuntimeError(f"Whisper transcription failed: {error_msg}")
            
            # Wait for thread to complete
            await transcription_task
            
            return chunk_index
            
        except Exception as e:
            # Ensure thread completes even on error
            await transcription_task
            raise e
    
    
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
        Process a transcription task by streaming batches to Redis.
        
        Main entry point that orchestrates the entire transcription flow:
        1. Initialize resources
        2. Download audio from MinIO
        3. Transcribe and stream batches to Redis as they're collected
        4. On error, send failed task to notify consumer
        5. Cleanup temp files
        
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
            
            # STEP 2: Transcribe and stream batches to Redis
            # Batches are sent immediately when CHUNK_BATCH_SIZE is reached
            # If transcription fails, a failed task is sent to notify consumer
            num_batches = await self._transcribe_and_stream_batches(
                audio_path=local_file,
                track_ref_id=track_ref_id,
            )
            
            # STEP 3: Log final results
            logger.info(
                f"\n{'='*60}\n"
                f"✅ TRANSCRIPTION COMPLETE - BATCHES STREAMED TO REDIS\n"
                f"{'='*60}\n"
                f"File: {task.filename}\n"
                f"Size: {file_size_mb:.2f} MB\n"
                f"Batches sent: {num_batches}\n"
                f"Track ID: {track_ref_id}\n"
                f"Status: All batches sent to Redis queue\n"
                f"{'='*60}"
            )
            
            return ""
            
        except Exception as e:
            logger.error(f"❌ Failed to process transcription: {e}")
            raise
            
        finally:
            # STEP 4: Always cleanup temp file
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