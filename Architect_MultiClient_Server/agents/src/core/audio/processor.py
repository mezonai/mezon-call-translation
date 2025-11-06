"""
Enhanced Audio Processing Pipeline

This module provides the main audio processing pipeline for voice activity detection
and audio chunk management. It integrates multiple processing stages including
preprocessing, VAD, and post-processing with comprehensive error handling and metrics.

Key Features:
    - Multi-stage audio pipeline architecture
    - Voice Activity Detection (VAD) with ZCR filtering
    - Buffer pooling for memory efficiency
    - Thread-safe queue management
    - Real-time metrics tracking
    - Graceful error handling and recovery

Classes:
    EnhancedVADProcessor: Main processor coordinating the audio pipeline
    
Dependencies:
    - AudioProcessorConfig: Configuration for audio processing parameters
    - AudioThreadingConfig: Threading configuration for async operations
    - AudioPipeline: Pipeline architecture for stage-based processing
"""
import asyncio
import numpy as np
from typing import Optional, List
import time
import threading
from src.config.audio_config import AudioProcessorConfig, AudioThreadingConfig
from src.utils.error_handling import AudioProcessingError, ErrorContext, ErrorSeverity
from src.logger import get_logger

logger = get_logger(__name__)
from src.utils.thread_safe.thread_safe_buffer import AudioBuffer, AudioChunk
from src.utils.thread_safe.thread_safe_queue import AudioQueue
from src.utils.vad.zcr_filter import EnhancedZCRFilter
from src.utils.resource_manager import AudioBufferPool
from src.services.metrics_service import MetricsService
from .audio_pipeline import (
    AudioPipeline,
    PreProcessingStage,
    VADProcessingStage,
    PostProcessingStage
)

class EnhancedVADProcessor:
    """Enhanced Voice Activity Detection Processor with pipeline architecture"""
    
    def __init__(self, config: AudioProcessorConfig, threading_config: AudioThreadingConfig):
        self.config = config
        self.threading_config = threading_config
        
        # Setup resources
        self.metrics = MetricsService.get_instance()
        self.buffer_pool = AudioBufferPool(
            max_size=threading_config.max_queue_size,
            buffer_size=self._calculate_buffer_size()
        )
        
        # Setup queues
        self.input_queue = AudioQueue(maxsize=threading_config.max_queue_size)
        self.processing_queue = AudioQueue(maxsize=threading_config.max_queue_size)
        self.output_queue = AudioQueue(maxsize=threading_config.max_queue_size)
        
        # Setup VAD
        self.vad = EnhancedZCRFilter(
            zcr_thresh=config.vad_config.zcr_thresh,
            ma_window=config.vad_config.ma_window,
            analysis_duration_ms=config.vad_config.analysis_duration_ms
        )
        
        # Setup pipeline
        self.pipeline = AudioPipeline(self.processing_queue, self.output_queue)
        self.pipeline.add_stage(PreProcessingStage(config.sample_rate))
        self.pipeline.add_stage(VADProcessingStage(self.vad))
        self.pipeline.add_stage(PostProcessingStage(batch_size=5))  # Changed from 3 to 5 for 50ms chunks
        
        # Setup state
        self.is_processing = False
        self._chunk_id = 0
        self._start_time = time.time()
    
    def _calculate_buffer_size(self) -> int:
        """Calculate buffer size based on config"""
        return int(self.config.sample_rate * self.config.chunk_duration_ms / 1000)
    
    async def process_audio_chunk(self, audio_data: np.ndarray) -> None:
        """Process a single audio chunk"""
        try:
            # Validate input
            if not isinstance(audio_data, np.ndarray):
                audio_data = np.array(audio_data, dtype=np.float32)
            
            # Create chunk with metadata
            chunk = AudioChunk(
                data=audio_data,
                timestamp=time.time(),
                chunk_id=self._get_next_chunk_id()
            )
            
            # Add to input queue
            await self.input_queue.put(chunk)
            self.metrics.track("vad.input_chunks", 1)
            
        except Exception as e:
            raise AudioProcessingError(
                f"Error processing audio chunk: {str(e)}",
                ErrorContext.create(
                    "EnhancedVADProcessor",
                    "process_audio_chunk",
                    ErrorSeverity.MEDIUM
                )
            )
    
    def _get_next_chunk_id(self) -> int:
        """Get next chunk ID"""
        self._chunk_id += 1
        return self._chunk_id
    
    async def start(self):
        """Start processing"""
        if self.is_processing:
            return
            
        self.is_processing = True
        self._start_time = time.time()
        
        # Start input processor
        asyncio.create_task(self._process_input())
        
        # Start pipeline
        asyncio.create_task(self.pipeline.start())
        
        # Start output processor
        asyncio.create_task(self._process_output())
        
        self.metrics.track("vad.started", 1)
    
    async def stop(self):
        """Stop processing"""
        self.is_processing = False
        self.pipeline.stop()
        
        # Clear queues
        self.input_queue.clear()
        self.processing_queue.clear()
        self.output_queue.clear()
        
        self.metrics.track("vad.stopped", 1)
    
    async def _process_input(self):
        """Process chunks from input queue to processing queue"""
        while self.is_processing:
            try:
                # Get chunk from input queue
                chunk = await self.input_queue.get(timeout=0.1)
                if chunk is None:
                    continue
                
                # Forward to processing queue
                await self.processing_queue.put(chunk)
                self.metrics.track("vad.chunks_to_processing", 1)
                
            except Exception as e:
                self.metrics.track("vad.input_errors", 1)
                # Log error but continue processing
                logger.error(f"Error in input processing: {e}", exc_info=True)
                await asyncio.sleep(0.001)
    
    async def _process_output(self):
        """Process chunks from output queue"""
        while self.is_processing:
            try:
                # Get processed chunk
                chunk = await self.output_queue.get(timeout=0.1)
                if chunk is None:
                    continue
                
                # Handle speech chunk (e.g., send to WebSocket)
                if chunk.is_speech:
                    self.metrics.track("vad.speech_chunks", 1)
                    # TODO: Send to WebSocket or callback
                
            except Exception as e:
                self.metrics.track("vad.output_errors", 1)
                logger.error(f"Error in output processing: {e}", exc_info=True)
    
    def get_stats(self) -> dict:
        """Get processing statistics"""
        pipeline_stats = self.pipeline.stats
        uptime = time.time() - self._start_time
        
        return {
            "uptime": uptime,
            "total_chunks": pipeline_stats.total_chunks,
            "processed_chunks": pipeline_stats.processed_chunks,
            "dropped_chunks": pipeline_stats.dropped_chunks,
            "avg_processing_time": pipeline_stats.avg_processing_time,
            "input_queue_size": self.input_queue.qsize(),
            "processing_queue_size": self.processing_queue.qsize(),
            "output_queue_size": self.output_queue.qsize()
        }
