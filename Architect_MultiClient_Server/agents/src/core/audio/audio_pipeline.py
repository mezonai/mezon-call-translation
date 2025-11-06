"""
Audio Processing Pipeline Architecture

This module implements a flexible, stage-based audio processing pipeline for
real-time audio stream handling. The pipeline allows chaining multiple processing
stages (preprocessing, VAD, post-processing) with automatic error handling and
performance metrics tracking.

Architecture:
    PipelineStage -> AudioPipeline -> Multiple Stages
    
Key Components:
    - PipelineStats: Performance metrics for pipeline monitoring
    - PipelineStage: Abstract base for processing stages
    - AudioPipeline: Main pipeline coordinator
    - PreProcessingStage: Audio normalization and preparation
    - VADProcessingStage: Voice Activity Detection stage
    - PostProcessingStage: Batching and final processing

Note: This module is deprecated in favor of processor.py which provides
a more integrated solution. Marked with "không được sử dụng" (do not use).
"""

from typing import List, Optional, Callable, Any
import threading
import time
import numpy as np
from dataclasses import dataclass
from src.utils.error_handling import AudioProcessingError, ErrorContext, ErrorSeverity
from src.services.metrics_service import MetricsService
from src.utils.thread_safe.thread_safe_buffer import AudioBuffer, AudioChunk
from src.utils.thread_safe.thread_safe_queue import AudioQueue

@dataclass
class PipelineStats:
    """Statistics for pipeline processing"""
    total_chunks: int = 0
    processed_chunks: int = 0
    dropped_chunks: int = 0
    total_processing_time: float = 0.0
    avg_processing_time: float = 0.0
    last_processed_time: float = 0.0

class PipelineStage:
    """Base class for pipeline stages"""
    
    def __init__(self, name: str):
        self.name = name
        self.metrics = MetricsService.get_instance()
        self.next_stage: Optional['PipelineStage'] = None
        self._processing_time = 0.0
        self._chunks_processed = 0
    
    async def process(self, chunk: AudioChunk) -> Optional[AudioChunk]:
        """Process an audio chunk"""
        raise NotImplementedError
    
    def _update_metrics(self, start_time: float):
        """Update processing metrics"""
        processing_time = time.time() - start_time
        self._processing_time += processing_time
        self._chunks_processed += 1
        
        self.metrics.track(f"pipeline.{self.name}.processing_time", processing_time)
        self.metrics.track(f"pipeline.{self.name}.chunks_processed", 1)
        if self._chunks_processed > 0:
            avg_time = self._processing_time / self._chunks_processed
            self.metrics.track(f"pipeline.{self.name}.avg_processing_time", avg_time)

class AudioPipeline:
    """Audio processing pipeline with multiple stages"""
    
    def __init__(self, input_queue: AudioQueue, output_queue: AudioQueue):
        self.input_queue = input_queue
        self.output_queue = output_queue
        self.stages: List[PipelineStage] = []
        self.stats = PipelineStats()
        self.metrics = MetricsService.get_instance()
        self._running = False
        self._lock = threading.Lock()
    
    def add_stage(self, stage: PipelineStage) -> 'AudioPipeline':
        """Add a processing stage to the pipeline"""
        with self._lock:
            if self.stages:
                self.stages[-1].next_stage = stage
            self.stages.append(stage)
        return self
    
    async def process_chunk(self, chunk: AudioChunk) -> Optional[AudioChunk]:
        """Process a chunk through all stages"""
        current_chunk = chunk
        start_time = time.time()
        
        try:
            for stage in self.stages:
                if current_chunk is None:
                    break
                current_chunk = await stage.process(current_chunk)
            
            # Update pipeline stats
            with self._lock:
                self.stats.total_chunks += 1
                if current_chunk is not None:
                    self.stats.processed_chunks += 1
                else:
                    self.stats.dropped_chunks += 1
                
                processing_time = time.time() - start_time
                self.stats.total_processing_time += processing_time
                self.stats.avg_processing_time = (
                    self.stats.total_processing_time / self.stats.total_chunks
                )
                self.stats.last_processed_time = time.time()
            
            # Update metrics
            self._update_pipeline_metrics(processing_time)
            
            return current_chunk
            
        except Exception as e:
            raise AudioProcessingError(
                f"Pipeline processing error: {str(e)}",
                ErrorContext.create(
                    "AudioPipeline",
                    "process_chunk",
                    ErrorSeverity.HIGH,
                    {"chunk_id": chunk.chunk_id}
                )
            )
    
    async def start(self):
        """Start pipeline processing"""
        self._running = True
        
        while self._running:
            try:
                # Get chunk from input queue
                chunk = await self.input_queue.get(timeout=0.1)
                if chunk is None:
                    continue
                
                # Process chunk through pipeline
                processed_chunk = await self.process_chunk(chunk)
                
                # Put processed chunk in output queue if valid
                if processed_chunk is not None:
                    await self.output_queue.put(processed_chunk)
                
            except Exception as e:
                self.metrics.track("pipeline.errors", 1)
                raise AudioProcessingError(
                    f"Pipeline error: {str(e)}",
                    ErrorContext.create(
                        "AudioPipeline",
                        "start",
                        ErrorSeverity.HIGH
                    )
                )
    
    def stop(self):
        """Stop pipeline processing"""
        self._running = False
    
    def _update_pipeline_metrics(self, processing_time: float):
        """Update pipeline metrics"""
        self.metrics.track("pipeline.total_chunks", self.stats.total_chunks)
        self.metrics.track("pipeline.processed_chunks", self.stats.processed_chunks)
        self.metrics.track("pipeline.dropped_chunks", self.stats.dropped_chunks)
        self.metrics.track("pipeline.processing_time", processing_time)
        self.metrics.track("pipeline.avg_processing_time", self.stats.avg_processing_time)

class PreProcessingStage(PipelineStage):
    """Audio pre-processing stage"""
    
    def __init__(self, sample_rate: int):
        super().__init__("pre_processing")
        self.sample_rate = sample_rate
    
    async def process(self, chunk: AudioChunk) -> Optional[AudioChunk]:
        start_time = time.time()
        
        try:
            # Ensure correct data type
            if not isinstance(chunk.data, np.ndarray):
                chunk.data = np.array(chunk.data, dtype=np.float32)
            
            # Normalize if needed
            if chunk.data.max() > 1.0 or chunk.data.min() < -1.0:
                chunk.data = chunk.data / 32767.0
            
            self._update_metrics(start_time)
            return chunk
            
        except Exception as e:
            raise AudioProcessingError(
                f"Pre-processing error: {str(e)}",
                ErrorContext.create(
                    "PreProcessingStage",
                    "process",
                    ErrorSeverity.MEDIUM
                )
            )

class VADProcessingStage(PipelineStage):
    """Voice Activity Detection processing stage"""
    
    def __init__(self, vad_processor: Any):
        super().__init__("vad_processing")
        self.vad = vad_processor
    
    async def process(self, chunk: AudioChunk) -> Optional[AudioChunk]:
        start_time = time.time()
        
        try:
            # Perform VAD
            is_speech, stats = self.vad.check(chunk.data)
            chunk.is_speech = is_speech
            
            # Update metrics
            self._update_metrics(start_time)
            self.metrics.track("vad.confidence", stats.confidence)
            
            # Only forward speech chunks
            return chunk if is_speech else None
            
        except Exception as e:
            raise AudioProcessingError(
                f"VAD processing error: {str(e)}",
                ErrorContext.create(
                    "VADProcessingStage",
                    "process",
                    ErrorSeverity.MEDIUM
                )
            )

class PostProcessingStage(PipelineStage):
    """Audio post-processing stage"""
    
    def __init__(self, batch_size: int = 3):
        super().__init__("post_processing")
        self.batch_size = batch_size
        self.batch_buffer = []
    
    async def process(self, chunk: AudioChunk) -> Optional[AudioChunk]:
        start_time = time.time()
        
        try:
            # Add to batch buffer
            self.batch_buffer.append(chunk)
            
            # Process if we have enough chunks
            if len(self.batch_buffer) >= self.batch_size:
                # Concatenate audio data
                batched_data = np.concatenate([c.data for c in self.batch_buffer])
                batched_chunk = AudioChunk(
                    data=batched_data,
                    is_speech=True,
                    timestamp=self.batch_buffer[0].timestamp,
                    chunk_id=self.batch_buffer[0].chunk_id
                )
                
                # Clear buffer
                self.batch_buffer = []
                
                self._update_metrics(start_time)
                return batched_chunk
            
            return None
            
        except Exception as e:
            raise AudioProcessingError(
                f"Post-processing error: {str(e)}",
                ErrorContext.create(
                    "PostProcessingStage",
                    "process",
                    ErrorSeverity.MEDIUM
                )
            )
