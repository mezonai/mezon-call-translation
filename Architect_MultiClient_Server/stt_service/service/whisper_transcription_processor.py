"""
Whisper Transcription Processor - STREAMING MODE ONLY

Progressive transcription with real-time saving:
1. Save track metadata immediately
2. Transcribe and save chunks progressively (every 200 segments)
3. Update final status
"""

import asyncio
import logging
import tempfile
import shutil
import math
from typing import Optional, List, Dict, Any
from pathlib import Path
from dataclasses import dataclass

from minio import Minio
from faster_whisper import WhisperModel
from bson import ObjectId

from stt_service.service.mongodb_service import get_mongodb_service

from ..config import get_config
from .transcription_queue_service import TranscriptionTask

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


class WhisperTranscriptionProcessor:
    """
    Processor that transcribes audio files using Whisper with progressive saving.
    
    Flow:
    1. Download audio from MinIO
    2. Save track metadata to MongoDB (status: "processing")
    3. Transcribe audio progressively, saving chunks every CHUNK_BATCH_SIZE segments
    4. Update track status to "completed" or "failed"
    5. Cleanup temp files
    """
    
    _instance: Optional['WhisperTranscriptionProcessor'] = None
    
    # Configuration
    CHUNK_BATCH_SIZE = 200  # Save to DB every 200 segments
    
    def __init__(self):
        self._config = get_config()
        self._minio_client: Optional[Minio] = None
        self._whisper_model: Optional[WhisperModel] = None
        self._initialized = False
        self._temp_dir: Optional[Path] = None
        self.mongodb_service = None
        
        logger.info("WhisperTranscriptionProcessor created (streaming mode)")
    
    @classmethod
    def get_instance(cls) -> 'WhisperTranscriptionProcessor':
        """Get singleton instance."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance
    
    async def initialize(self):
        """Initialize MinIO client, Whisper model, MongoDB, and temp directory."""
        if self._initialized:
            return
        
        logger.info("Initializing WhisperTranscriptionProcessor...")
        
        # Initialize MongoDB
        self.mongodb_service = get_mongodb_service()
        await self.mongodb_service.connect()
        logger.info("✅ MongoDB connected")
        
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
    
    async def _transcribe_with_progressive_saving(
        self,
        audio_path: Path,
        track_ref_id: ObjectId,
    ) -> tuple[str, int, Dict[str, Any]]:
        """
        Transcribe audio file with progressive chunk saving.
        
        This method:
        1. Starts Whisper transcription
        2. Collects segments in batches of CHUNK_BATCH_SIZE
        3. Saves each batch to MongoDB immediately
        4. Returns full text and info when complete
        
        Args:
            audio_path: Path to audio file on disk
            track_ref_id: MongoDB ObjectId of track document
            
        Returns:
            Tuple of (full_text, total_segments, info_dict)
        """
        if not self._whisper_model:
            raise RuntimeError("Whisper model not initialized")
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        
        whisper_config = self._config.whisper
        logger.info(f"🎤 Starting progressive transcription for {audio_path.name}...")
        
        # Buffer for batch saving
        segment_buffer: List[Dict[str, Any]] = []
        full_text_parts: List[str] = []
        total_segments = 0
        
        try:
            # Start transcription (returns generator)
            segments_generator, info = self._whisper_model.transcribe(
                str(audio_path),
                language=whisper_config.language if whisper_config.language else None,
                beam_size=whisper_config.beam_size,
                vad_filter=whisper_config.vad_filter,
            )
            
            # Process segments as they're generated
            for seg in segments_generator:
                # Convert to our format
                segment = TranscriptionSegment(
                    start=seg.start,
                    end=seg.end,
                    text=seg.text.strip(),
                    confidence=round(math.exp(seg.avg_logprob), 4)
                )
                
                # Add to buffer
                segment_buffer.append(segment.to_dict())
                full_text_parts.append(segment.text)
                total_segments += 1
                
                # Save batch when buffer is full
                if len(segment_buffer) >= self.CHUNK_BATCH_SIZE:
                    await self.mongodb_service.append_transcript_chunk(
                        track_ref_id=track_ref_id,
                        new_segments=segment_buffer
                    )
                    
                    # Clear buffer after successful save
                    segment_buffer.clear()
            
            # Save remaining segments (last batch)
            if segment_buffer:
                logger.info(
                    f"💾 Saving final batch of {len(segment_buffer)} segments "
                    f"(total: {total_segments})..."
                )
                await self.mongodb_service.append_transcript_chunk(
                    track_ref_id=track_ref_id,
                    new_segments=segment_buffer
                )
                segment_buffer.clear()
            
            # Build full text
            full_text = " ".join(full_text_parts)
            
            # Build info dict
            lang_prob = info.language_probability
            if math.isnan(lang_prob):
                lang_prob = 0.0
            
            info_dict = {
                "language": info.language,
                "language_probability": lang_prob,
                "duration": info.duration,
                "duration_after_vad": info.duration_after_vad,
            }
            
            logger.info(
                f"✅ Transcription complete: {total_segments} segments, "
                f"{len(full_text)} characters"
            )
            
            return full_text, total_segments, info_dict
            
        except Exception as e:
            logger.error(f"Failed during transcription: {e}")
            raise RuntimeError(f"Transcription failed: {e}") from e
    
    
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
        Process a transcription task with progressive saving.
        
        Main entry point that orchestrates the entire transcription flow:
        1. Initialize resources
        2. Download audio from MinIO
        3. Save track metadata (status: "processing")
        4. Transcribe with progressive chunk saving
        5. Update status to "completed"
        6. Cleanup temp files
        
        Args:
            task: TranscriptionTask with file information
            
        Returns:
            Full transcribed text
            
        Raises:
            RuntimeError: If any step fails
        """
        await self.initialize()
        
        logger.info(f"🎯 Processing transcription task: {task.filename}")
        logger.info(
            f"   Egress: {task.egress_id}\n"
            f"   Track: {task.track_id}\n"
            f"   Room_id: {task.room_id}\n"
            f"   Participant: {task.participant_identity}"
        )
        
        local_file: Optional[Path] = None
        track_ref_id: Optional[ObjectId] = None
        
        try:
            # STEP 1: Download audio from MinIO
            local_file, file_size_mb = await self._download_from_minio(task.filename)
            
            # STEP 2: Save track metadata immediately
            audio_data = {
                "filename": task.filename,
                "duration_sec": task.duration,
                "started_at_ns": task.started_at,
                "ended_at_ns": task.ended_at
            }
            
            logger.info("💾 Saving track metadata (status: processing)...")
            
            track_ref_id = await self.mongodb_service.save_track_metadata(
                egress_id=task.egress_id,
                track_id=task.track_id,
                room_ref_id=task.room_id,
                participant_identity=task.participant_identity,
                audio_info=audio_data,
                status="processing"
            )
            
            if not track_ref_id:
                raise RuntimeError("Failed to save track metadata")
            
            logger.info(f"✅ Track metadata saved: track_ref_id={track_ref_id}")
            
            # STEP 3: Transcribe with progressive saving
            full_text, num_segments, info = await self._transcribe_with_progressive_saving(
                audio_path=local_file,
                track_ref_id=track_ref_id
            )
            
            # STEP 4: Update status to completed
            logger.info("✅ Updating track status to 'completed'...")
            
            success = await self.mongodb_service.update_track_status(
                track_ref_id=track_ref_id,
                room_ref_id=task.room_id,
                status="completed"
            )
            
            if not success:
                logger.warning("Failed to update status to 'completed'")
            
            # STEP 5: Log final results
            lang_prob = info['language_probability']
            lang_prob_str = f"{lang_prob:.1%}" if not math.isnan(lang_prob) else "N/A"
            
            logger.info(
                f"\n{'='*60}\n"
                f"✅ TRANSCRIPTION COMPLETE\n"
                f"{'='*60}\n"
                f"File: {task.filename}\n"
                f"Size: {file_size_mb:.2f} MB\n"
                f"Language: {info['language']} (confidence: {lang_prob_str})\n"
                f"Duration: {info['duration']:.2f}s\n"
                f"Duration after VAD: {info['duration_after_vad']:.2f}s\n"
                f"Total segments: {num_segments}\n"
                f"Text length: {len(full_text)} characters\n"
                f"Track ID: {track_ref_id}\n"
                f"Status: completed\n"
                f"{'='*60}"
            )
            
            return full_text
            
        except Exception as e:
            logger.error(f"❌ Failed to process transcription: {e}")
            
            # Update status to failed if we created metadata
            if track_ref_id:
                logger.info("Updating track status to 'failed'...")
                try:
                    await self.mongodb_service.update_track_status(
                        track_ref_id=track_ref_id,
                        room_ref_id=task.room_id,
                        status="failed"
                    )
                except Exception as update_error:
                    logger.error(f"Failed to update status to 'failed': {update_error}")
            
            raise
            
        finally:
            # ============================================================
            # STEP 6: Always cleanup temp file
            # ============================================================
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
        
        # Disconnect MongoDB
        if self.mongodb_service:
            await self.mongodb_service.disconnect()
        
        # Reset state
        self._whisper_model = None
        self._minio_client = None
        self._initialized = False
        
        logger.info("✅ WhisperTranscriptionProcessor shutdown complete")


# ============================================================
# PUBLIC API
# ============================================================

def get_whisper_processor() -> WhisperTranscriptionProcessor:
    """Get the singleton Whisper processor instance."""
    return WhisperTranscriptionProcessor.get_instance()


async def transcribe_task(task: TranscriptionTask) -> str:
    """
    Convenience function to transcribe a task.
    
    Can be used directly as the processor for TranscriptionQueueService:
        queue_service.set_processor(transcribe_task)
    
    Args:
        task: TranscriptionTask to process
        
    Returns:
        Full transcribed text
    """
    processor = get_whisper_processor()
    return await processor.process(task)