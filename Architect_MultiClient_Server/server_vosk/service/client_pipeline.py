
import asyncio
import json
import threading
import time
import logging
import queue
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass
from enum import Enum
from vosk import Model, KaldiRecognizer

from ..utils.circuit_breaker import get_stt_circuit_breaker
from ..config import get_config

logger = logging.getLogger(__name__)


class PipelineState(Enum):
    """Pipeline states"""
    INITIALIZING = "initializing"
    ACTIVE = "active"
    IDLE = "idle"
    SHUTTING_DOWN = "shutting_down"
    TERMINATED = "terminated"


@dataclass
class ClientPipelineStats:
    """Statistics for a client pipeline"""
    created_at: float
    last_activity: float
    chunks_processed: int
    final_results: int
    partial_results: int
    avg_processing_time_ms: float
    total_audio_duration_ms: float
    state: PipelineState


class ClientInferencePipeline:
    """Dedicated inference pipeline for a single client
    
    Each client gets:
    - Dedicated thread for continuous processing
    - Separate audio buffer/queue
    - Own Vosk recognizer instance
    - Independent processing state
    """
    
    def __init__(self, client_id: str, session_id: str, model: Model, 
                 result_callback: Callable[[str, Dict], None], pipeline_manager=None,
                 result_dispatcher=None):  # NEW parameter
        self.client_id = client_id
        self.session_id = session_id
        self.model = model
        self.result_callback = result_callback
        self.result_dispatcher = result_dispatcher  # NEW: Store reference
        self.pipeline_manager = pipeline_manager  # Reference to get concurrent client count
        
        # Store main event loop during initialization
        try:
            self._main_loop = asyncio.get_event_loop()
            logger.debug(f"Client {self.client_id}: Stored main event loop during initialization")
        except Exception as e:
            logger.warning(f"Client {self.client_id}: Could not store main event loop: {e}")
        
        # Get configuration
        self.config = get_config()
        
        # Initialize Vosk recognizer (DEDICATED per client)
        self.recognizer = KaldiRecognizer(self.model, self.config.audio.sample_rate)
        logger.info(f"Created dedicated Vosk recognizer for client {client_id}")
        
        # Pipeline state
        self.state = PipelineState.INITIALIZING
        self.created_at = time.time()
        self.last_activity = time.time()
        
        # Audio processing (INDIVIDUAL per client)
        self.audio_queue = queue.Queue(maxsize=self.config.queue.audio_queue_maxsize)
        self.accumulated_chunks: List[bytes] = []  # Client's own buffer
        self.accumulated_size = 0
        self.last_process_time = time.time()
        self.last_chunk_id: Optional[int] = None  # Track last observed chunk id for emission
        
        logger.info(f"Created individual audio buffer (max_size={self.config.queue.audio_queue_maxsize}) for client {client_id}")
        
        # Statistics
        self.stats = ClientPipelineStats(
            created_at=self.created_at,
            last_activity=self.last_activity,
            chunks_processed=0,
            final_results=0,
            partial_results=0,
            avg_processing_time_ms=0.0,
            total_audio_duration_ms=0.0,
            state=self.state
        )
        
        # Processing control (DEDICATED THREAD per client)
        self._processing_thread: Optional[threading.Thread] = None
        self._shutdown_event = threading.Event()
        self._lock = threading.Lock()
        
        # Circuit breaker (per client) with disconnect callback
        self._circuit_breaker = get_stt_circuit_breaker()
        # Set up circuit breaker callback for disconnect
        def _on_threshold_reached():
            logger.error(f"🚨 Circuit breaker threshold reached for client {self.client_id} - Initiating disconnect")
            if self._main_loop and not self._shutdown_event.is_set():
                asyncio.run_coroutine_threadsafe(self.shutdown(timeout=1.0), self._main_loop)
        self._circuit_breaker._on_threshold_reached = _on_threshold_reached
        
        logger.info(f"✅ Created dedicated inference pipeline for client {client_id} in session {session_id}")
    
    def _get_adaptive_chunk_size(self) -> int:
        """Get adaptive chunk size based on concurrent clients"""
        if not self.pipeline_manager:
            # Fallback to config default if no pipeline manager
            return self.config.stt.max_chunks
        
        # Get current active client count
        try:
            concurrent_clients = len(self.pipeline_manager.pipelines)
        except:
            concurrent_clients = 1  # Safe fallback
        
        # Adaptive chunk sizing based on performance data
        if concurrent_clients <= 2:
            # 1-2 clients: 100-200ms latency, minimal overhead
            chunk_size = 4  # ~100-200ms worth of audio chunks
            reason = "Low latency, minimal overhead"
        elif concurrent_clients <= 4:
            # 3-4 clients: 200-300ms, balanced performance
            chunk_size = 2  # ~200-300ms worth of audio chunks  
            reason = "Balanced performance, stable scaling"
        else:
            # 5-8 clients: 200ms, OPTIMAL sweet spot
            chunk_size = 1  # ~200ms worth of audio chunks
            reason = "OPTIMAL - Sweet spot performance"
        
        logger.debug(f"Client {self.client_id}: Adaptive chunk size = {chunk_size} (clients={concurrent_clients})")
        return 4
    
    async def start(self):
        """Start the pipeline processing"""
        if self.state != PipelineState.INITIALIZING:
            raise RuntimeError(f"Cannot start pipeline in state {self.state}")
        
        self.state = PipelineState.ACTIVE
        self._processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        self._processing_thread.start()
        logger.info(f"✅ Started dedicated processing thread for client {self.client_id}")
    
    async def submit_audio(self, audio_chunk: bytes, chunk_id: Optional[int] = None) -> bool:
        """Submit audio chunk for processing with optional chunk_id"""
        # Check pipeline state first

        if self.state not in [PipelineState.ACTIVE, PipelineState.IDLE]:
            logger.debug(f"Cannot submit audio to pipeline in state {self.state} for client {self.client_id}")
            return False
        
        try:
            # Check circuit breaker
            if not self._circuit_breaker.can_try():
                circuit_state = self._circuit_breaker.get_state()
                time_since_failure = circuit_state.get('time_since_last_failure', 0)
                failure_count = circuit_state.get('failure_count', 0)
                
                logger.error(
                    f"🚨 Pipeline circuit breaker BLOCKING audio for client {self.client_id}! 🚨\n"
                    f"   Circuit State: {circuit_state.get('state')}\n"
                    f"   Failure Count: {failure_count}/{self._circuit_breaker.config.failure_threshold}\n"
                    f"   Time Since Last Failure: {time_since_failure:.1f}s\n"
                    f"   Timeout Required: {self._circuit_breaker.config.timeout}s\n"
                    f"   Pipeline Issues: Check Vosk processing errors, audio format issues, or model problems\n"
                    f"   ⚠️  This client's audio processing is suspended until circuit recovers"
                )
                return False
            
            # Add to individual client queue (non-blocking). Support optional chunk_id.
            if chunk_id is not None:
                self.audio_queue.put_nowait((chunk_id, audio_chunk))
            else:
                self.audio_queue.put_nowait(audio_chunk)
            self.last_activity = time.time()

            
            # Update state to active if idle (log only state change)
            if self.state == PipelineState.IDLE:
                self.state = PipelineState.ACTIVE
                logger.info(f"Client {self.client_id}: Pipeline activated")
            
            return True
            
        except queue.Full:
            logger.warning(f"Individual audio buffer full for client {self.client_id}, dropping chunk")
            return False
        except Exception as e:
            logger.error(
                f"🚫 PIPELINE CIRCUIT BREAKER FAILURE for client {self.client_id}\n"
                f"   Exception during audio submission: {type(e).__name__}: {e}\n"
                f"   This indicates pipeline-level processing error\n"
                f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}"
            )
            self._circuit_breaker.record_failure()
            return False
    
    def _processing_loop(self):
        """Main processing loop for the pipeline - runs in dedicated thread"""
        logger.info(f"Client {self.client_id}: Processing loop started in thread {threading.current_thread().name}")
        
        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for audio chunk or timeout
                    try:
                        item = self.audio_queue.get(timeout=1.0)  # 1 second timeout
                        # Unpack optional (chunk_id, chunk) tuple
                        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
                            chunk_id, chunk = item
                            if isinstance(chunk_id, int):
                                self.last_chunk_id = chunk_id
                        else:
                            chunk = item
                        # Remove frequent chunk processing log
                    except queue.Empty:
                        # Check if we should go idle
                        if time.time() - self.last_activity > 5.0:  # 5 seconds idle
                            if self.state == PipelineState.ACTIVE:
                                self.state = PipelineState.IDLE
                                logger.info(f"Client {self.client_id}: Pipeline went idle after 5s inactivity")
                        continue
                    
                    # Process the chunk
                    self._process_audio_chunk(chunk)
                    
                except Exception as e:
                    logger.error(
                        f"🚫 PIPELINE CIRCUIT BREAKER FAILURE for client {self.client_id}\n"
                        f"   Exception in processing loop: {type(e).__name__}: {e}\n"
                        f"   This indicates audio chunk processing error\n"
                        f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}"
                    )
                    self._circuit_breaker.record_failure()
                    time.sleep(0.1)  # Brief pause on error
            
        except Exception as e:
            logger.error(f"Client {self.client_id}: Fatal error in processing loop: {e}")
        finally:
            self.state = PipelineState.TERMINATED
            logger.info(f"Client {self.client_id}: Processing loop terminated")
    
    def _process_audio_chunk(self, chunk: bytes):
        """Process a single audio chunk"""
        with self._lock:
            start_time = time.time()
            
            try:
                # Accumulate chunk (no logging for each chunk)
                self.accumulated_chunks.append(chunk)
                self.accumulated_size += len(chunk)
                
                # Check if we should process accumulated chunks
                now = time.time()
                time_since_last_process = now - self.last_process_time
                
                # Only process when reaching adaptive chunk threshold
                adaptive_chunk_size = self._get_adaptive_chunk_size()
                should_process = len(self.accumulated_chunks) >= adaptive_chunk_size
                
                # Only log when actually processing (not every chunk)
                if should_process and self.accumulated_chunks:
                    self._process_accumulated_chunks()
                    self.last_process_time = now
                
                # Update statistics
                processing_time = (time.time() - start_time) * 1000  # ms
                self.stats.chunks_processed += 1
                self.stats.avg_processing_time_ms = (
                    (self.stats.avg_processing_time_ms * (self.stats.chunks_processed - 1) + processing_time) /
                    self.stats.chunks_processed
                )
                self.stats.last_activity = time.time()
                
                # Record success for circuit breaker
                self._circuit_breaker.record_success()
                
            except Exception as e:
                logger.error(
                    f"🚫 PIPELINE CIRCUIT BREAKER FAILURE for client {self.client_id}\n"
                    f"   Exception during chunk processing: {type(e).__name__}: {e}\n"
                    f"   Possible causes: Vosk processing error, audio format issue, memory error\n"
                    f"   Current failures: {self._circuit_breaker.failure_count + 1}/{self._circuit_breaker.config.failure_threshold}"
                )
                self._circuit_breaker.record_failure()
                raise
    
    def _process_accumulated_chunks(self):
        """Process all accumulated chunks"""
        if not self.accumulated_chunks:
            logger.warning(f"Client {self.client_id}: No accumulated chunks to process")
            return
        
        try:
            # Merge all accumulated chunks
            merged_chunk = b''.join(self.accumulated_chunks)
            chunk_count = len(self.accumulated_chunks)
            
            # Clear accumulator
            self.accumulated_chunks.clear()
            self.accumulated_size = 0
            
            start_time = time.time()

            # Process with Vosk (DIRECT call in dedicated thread)
            is_final = self.recognizer.AcceptWaveform(merged_chunk)
            
            chunk_size = len(merged_chunk)
            duration_ms = (chunk_size *1000)/ (self.config.audio.sample_rate* 2) 
            processing_ms = (time.time() - start_time) * 1000
            logger.debug(
                f"Client {self.client_id} | Chunk Size: {chunk_size} bytes | "
                f"Duration: {duration_ms:.2f} ms | Processing: {processing_ms:.2f} ms | buffer audio: {self.audio_queue.qsize()}"
            )
            if(duration_ms < processing_ms):
                logger.warning(f"Client {self.client_id}: Audio processing time exceeded chunk duration! {processing_ms:.2f} ms > {duration_ms:.2f} ms")

            if is_final:
                # Final result
                result = json.loads(self.recognizer.Result())
                text = result.get("text", "").strip()
                if len(text) >= self.config.audio.min_text_length:
                    # Format phù hợp với client
                    result_payload = {
                        "text": text,
                        "is_final": True,
                        "client_id": self.client_id,
                        "session_id": self.session_id,
                        "timestamp": time.time()
                    }
                    if self.last_chunk_id is not None:
                        result_payload["chunk_id"] = self.last_chunk_id

                    self._emit_result("transcript", result_payload)
                    logger.debug(f"Emitting final result for client {self.client_id}: {text[:50]}...")
                    self.stats.final_results += 1
            else:
                # Partial result
                partial = json.loads(self.recognizer.PartialResult())
                text = partial.get("partial", "").strip()
                if len(text) >= self.config.audio.min_text_length:
                    # Format phù hợp với client
                    result_payload = {
                        "text": text,
                        "is_final": False,
                        "client_id": self.client_id,
                        "session_id": self.session_id,
                        "timestamp": time.time()
                    }
                    if self.last_chunk_id is not None:
                        result_payload["chunk_id"] = self.last_chunk_id

                    self._emit_result("transcript", result_payload)
                    logger.debug(f"Emitting partial result for client {self.client_id}: {text[:50]}...")
                    self.stats.partial_results += 1
        
        except Exception as e:
            logger.error(f"Client {self.client_id}: Error processing accumulated chunks: {e}", exc_info=True)
            raise
    
    def _emit_result(self, result_type: str, payload: Dict):
        """Emit processing result using optimized dispatcher (thread-safe)"""
        try:
            # Prefer optimized dispatcher
            if self.result_dispatcher:
                coro = self.result_dispatcher.emit_result(
                    session_id=self.session_id,
                    client_id=self.client_id,
                    result_type=result_type,
                    payload=payload
                )
                
                # Store the main event loop during initialization for thread-safe access
                if not hasattr(self, '_main_loop'):
                    try:
                        import threading
                        if threading.current_thread() is threading.main_thread():
                            self._main_loop = asyncio.get_event_loop()
                        else:
                            # If we're in a different thread, try to get the running loop from main thread
                            for thread in threading.enumerate():
                                if thread is threading.main_thread():
                                    try:
                                        self._main_loop = asyncio.get_running_loop()
                                        break
                                    except RuntimeError:
                                        pass
                    except Exception as e:
                        logger.error(f"Failed to get main event loop: {e}")
                
                if hasattr(self, '_main_loop'):
                    try:
                        asyncio.run_coroutine_threadsafe(coro, self._main_loop)
                        return
                    except Exception as e:
                        logger.error(f"Failed to run coroutine in main loop: {e}")
                else:
                    logger.error("No main event loop available for async dispatch")
            
            # Fallback to legacy callback
            if self.result_callback:
                self.result_callback(result_type, payload)
            else:
                logger.error(f"Client {self.client_id}: NO RESULT OUTPUT CONFIGURED!")
            
        except Exception as e:
            logger.error(f"Client {self.client_id}: Error emitting result: {e}", exc_info=True)
    
    async def shutdown(self, timeout: float = 5.0):
        """Shutdown the pipeline gracefully"""
        if self.state == PipelineState.TERMINATED:
            return
        
        logger.info(f"Client {self.client_id}: Shutting down pipeline")
        self.state = PipelineState.SHUTTING_DOWN
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Wait for processing thread to complete
        if self._processing_thread and self._processing_thread.is_alive():
            self._processing_thread.join(timeout=timeout)
            if self._processing_thread.is_alive():
                logger.warning(f"Client {self.client_id}: Shutdown timeout, thread still alive")
        
        # Process any remaining chunks
        try:
            if self.accumulated_chunks:
                self._process_accumulated_chunks()
        except Exception as e:
            logger.error(f"Client {self.client_id}: Error processing final chunks: {e}")
        
        self.state = PipelineState.TERMINATED
        logger.info(f"Client {self.client_id}: Pipeline shutdown complete")
    
    def get_stats(self) -> ClientPipelineStats:
        """Get current pipeline statistics"""
        self.stats.state = self.state
        return self.stats
    
    def is_idle(self, idle_threshold: float = 30.0) -> bool:
        """Check if pipeline has been idle for too long"""
        return (time.time() - self.last_activity) > idle_threshold
    
    def get_info(self) -> Dict[str, Any]:
        """Get pipeline information"""
        return {
            "client_id": self.client_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "last_activity": self.last_activity,
            "uptime_seconds": time.time() - self.created_at,
            "idle_seconds": time.time() - self.last_activity,
            "queue_size": self.audio_queue.qsize(),
            "accumulated_chunks": len(self.accumulated_chunks),
            "thread_name": self._processing_thread.name if self._processing_thread else "unknown",
            "stats": {
                "chunks_processed": self.stats.chunks_processed,
                "final_results": self.stats.final_results,
                "partial_results": self.stats.partial_results,
                "avg_processing_time_ms": round(self.stats.avg_processing_time_ms, 2)
            }
        }