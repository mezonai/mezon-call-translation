import json
import os
import time
import queue
import threading
import logging
from vosk import Model, KaldiRecognizer
from .health_service import register_stt_health_checks
from ..utils.circuit_breaker import get_stt_circuit_breaker, CircuitBreakerOpenException
from ..config import get_config
import numpy as np
from typing import Dict, Tuple, Optional, Any
from dataclasses import dataclass

from dotenv import load_dotenv
import asyncio
from .. import session_manager

logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()


# STTConfig is now imported from config package

class STTVoskService:
    def __init__(self, model_path: Optional[str] = None):
        logger.info("Initializing STTVoskService...")
        
        # Get configuration
        self.config = get_config()
        
        # Use config for model path
        model_path =  os.getenv('VOSK_MODEL_PATH', 'model/Transcription/en-model')
        
        if not os.path.exists(model_path):
            raise FileNotFoundError(f"VOSK model not found at {model_path}")
        print(f"Loading VOSK model from {model_path}...")
        self.model = Model(model_path)

        # Thread safety locks
        self._accumulated_chunks_lock = threading.RLock()
        self._client_states_lock = threading.RLock()
        self._stats_lock = threading.RLock()
        
        # Per-worker recognizers/state
        self.num_workers = self.config.stt.num_workers
        self.worker_recognizers = [dict() for _ in range(self.num_workers)]
        self.worker_client_state = [dict() for _ in range(self.num_workers)]

        # Per-worker queues
        self.worker_queues = [queue.Queue(maxsize=self.config.queue.audio_queue_maxsize) for _ in range(self.num_workers)]
        # Legacy fallback result queue (sync). Prefer async_result_queue when set from main.
        self.result_queue = queue.Queue(maxsize=self.config.queue.result_queue_maxsize)
        self.async_result_queue = None  # set by main via set_async_result_queue
        self.async_loop = None
        self.stop_event = threading.Event()
        
        # Thread-safe accumulated chunks storage
        self.accumulated_chunks: Dict[str, Tuple[list, int, str, float]] = {}

        # Initialize circuit breaker only (VAD removed)
        self._circuit_breaker = get_stt_circuit_breaker()
        logger.info("Circuit breaker initialized successfully")

        # Start worker threads
        self.worker_threads = []
        for idx in range(self.num_workers):
            t = threading.Thread(target=self.stt_worker, args=(idx,), daemon=True)
            t.start()
            self.worker_threads.append(t)

        # Per-worker stats for metrics
        self.worker_stats = [
            {
                "avg_wait_ms": 0.0,
                "processed": 0,
                "last_chunk_duration_ms": 0,
                "last_speech_prob": 0.0,
                "vad_filtered_chunks": 0,
                "total_chunks": 0
            }
            for _ in range(self.num_workers)
        ]

        # Metrics thread
        self.metrics_thread = threading.Thread(target=self._metrics_loop, daemon=True)
        self.metrics_thread.start()
        
        # Cleanup thread for resource management
        self.cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self.cleanup_thread.start()
        
        # Register health checks
        register_stt_health_checks(self)

    def set_async_result_queue(self, loop, async_queue):
        """Register asyncio loop and queue for non-polling result dispatch."""
        self.async_loop = loop
        self.async_result_queue = async_queue

    def _emit_result(self, result_type, payload, worker_index):
        """Thread-safe result emission. Prefer asyncio queue if available."""
        if self.async_result_queue is not None and self.async_loop is not None:
            try:
                self.async_loop.call_soon_threadsafe(self.async_result_queue.put_nowait, (result_type, payload))
            except Exception:
                # Fallback to sync queue if async queue is unavailable/full
                try:
                    self.result_queue.put((result_type, payload))
                except Exception:
                    logger.exception("[worker=%s] Failed to emit result (both queues)", worker_index)
        else:
            try:
                # Ensure the result is sent to the correct WebSocket client
                client_ws = session_manager.get_client_websocket(payload.get("session_id"), payload.get("client_id"))
                if client_ws:
                    asyncio.run_coroutine_threadsafe(client_ws.send(json.dumps(payload)), self.async_loop)
            except Exception:
                logger.exception("[worker=%s] Failed to emit result (sync queue)", worker_index)

    def get_or_create_recognizer(self, worker_index: int, client_id: str, session_id: str):
        """Thread-safe method to get or create recognizer for a client."""
        key = (client_id, session_id)
        recognizers = self.worker_recognizers[worker_index]
        states = self.worker_client_state[worker_index]
        
        with self._client_states_lock:
            if key not in recognizers:
                logger.info("[worker=%s] Creating recognizer for %s", worker_index, key)
                recognizers[key] = KaldiRecognizer(self.model, self.config.audio.sample_rate)
                recognizers[key].SetWords(False)
                states[key] = {
                    "last_translation_time": time.time(),
                    "last_queued_text": "",
                    "is_final": False
                }
            return recognizers[key], states[key]

    def stt_worker(self, worker_index: int):
        """Thread-safe STT worker with improved resource management."""
        q = self.worker_queues[worker_index]
        
        while not self.stop_event.is_set():
            try:
                enqueue_time, chunk, client_id, session_id = q.get(timeout=0.1)
                
                # Update wait time metric (EMA)
                now = time.time()
                wait_ms = max(0.0, (now - enqueue_time) * 1000.0)
                with self._stats_lock:
                    s = self.worker_stats[worker_index]
                    s["avg_wait_ms"] = (s["avg_wait_ms"] * 0.9) + (wait_ms * 0.1)
                
                # Thread-safe chunk accumulation
                with self._accumulated_chunks_lock:
                    if client_id not in self.accumulated_chunks:
                        self.accumulated_chunks[client_id] = ([], 0, session_id, now)
                    
                    chunks, total_size, _, last_process_time = self.accumulated_chunks[client_id]
                    chunks.append(chunk)
                    total_size += len(chunk)
                    
                    # Calculate processing conditions based on queue status
                    queue_load = q.qsize() / self.config.queue.audio_queue_maxsize
                    time_since_last_process = now - last_process_time

                    # Adjust processing thresholds based on queue load
                    if queue_load >= self.config.stt.queue_load_high:  # Very high load (>70%)
                        chunks_threshold = self.config.stt.max_chunks
                        time_threshold = self.config.stt.max_time_threshold
                        logger.debug(f"High load mode ({queue_load:.1%}): fast processing")
                    
                    elif queue_load >= self.config.stt.queue_load_medium:  # High load (50-70%)
                        chunks_threshold = min(self.config.stt.max_chunks, int(self.config.stt.min_chunks * 4))
                        time_threshold = self.config.stt.min_time_threshold * 3
                        logger.debug(f"Medium load mode ({queue_load:.1%}): quick processing")
                    
                    elif queue_load >= self.config.stt.queue_load_low:  # Medium load (20-50%)
                        chunks_threshold = min(self.config.stt.max_chunks, int(self.config.stt.min_chunks * 2))
                        time_threshold =  self.config.stt.max_time_threshold * 2
                        logger.debug(f"Balanced mode ({queue_load:.1%}): normal processing")
                    
                    else:  # Low load (<20%)
                        chunks_threshold = self.config.stt.min_chunks
                        time_threshold = self.config.stt.min_time_threshold
                        logger.debug(f"Low load mode ({queue_load:.1%}): quality processing")
                    
                    # Decision to process
                    should_process = (
                        len(chunks) >= chunks_threshold or
                        time_since_last_process >= time_threshold or
                        q.qsize() == 0
                    )
                    
                    # Update accumulated chunks
                    self.accumulated_chunks[client_id] = (chunks, total_size, session_id, last_process_time)
                    
                    if should_process:
                        chunks, total_size, session_id, _ = self.accumulated_chunks[client_id]
                        # Merge all accumulated chunks
                        merged_chunk = b''.join(chunks)
                        
                        # Get or create recognizer
                        recognizer, state = self.get_or_create_recognizer(worker_index, client_id, session_id)
                        
                        # Log worker state
                        logger.debug(
                            "[Worker %d] Processing accumulated: client=%s chunks=%d total_size=%d queue_load=%.1f%% wait=%.1fms",
                            worker_index, client_id, len(chunks), total_size, 
                            (q.qsize() / self.config.queue.audio_queue_maxsize) * 100, wait_ms
                        )
                        
                        # Reset accumulator with new processing time
                        self.accumulated_chunks[client_id] = ([], 0, session_id, now)
                
                        # Process merged chunk
                        logger.debug("[worker=%s] Processing merged chunk for client=%s: size=%s", 
                                   worker_index, client_id, len(merged_chunk))
                        is_final = recognizer.AcceptWaveform(merged_chunk)
                        logger.debug("[worker=%s] AcceptWaveform result for merged chunk: %s", 
                                   worker_index, is_final)
                    
                        if is_final:
                            result = json.loads(recognizer.Result())
                            logger.debug("[worker=%s] Raw Vosk final result: %s", worker_index, result)
                            text = result.get("text", "").strip()
                            logger.debug("[worker=%s] Final result for client=%s: text='%s', len=%s", worker_index, client_id, text, len(text))
                            if len(text) >= self.config.audio.min_text_length and text.lower() != "the":
                                # Kiểm tra text có khác với last_text không
                                last_text = state.get("last_queued_text", "")
                                if text != last_text or is_final != state.get("is_final", False):  # Chỉ emit nếu text mới khác text cũ
                                    self._emit_result("transcripts", {
                                        "type": "transcripts",
                                        "text": text,
                                        "is_final": True,
                                        "session_id": session_id,
                                        "client_id": client_id
                                    }, worker_index)
                                    self.queue_translation(text, client_id, session_id, state, is_final=True)
                                else:
                                    logger.debug("[worker=%s] Skipped duplicate final result for client=%s: '%s'", 
                                            worker_index, client_id, text)
                        else:
                            partial = json.loads(recognizer.PartialResult())
                            logger.debug("[worker=%s] Raw Vosk partial result: %s", worker_index, partial)
                            text = partial.get("partial", "").strip()
                            logger.debug("[worker=%s] Partial result for client=%s: text='%s', len=%s", worker_index, client_id, text, len(text))
                            if text and text.lower() != "the":
                                # Kiểm tra text có khác với last_text không
                                last_text = state.get("last_queued_text", "")
                                # Chỉ emit nếu text mới khác text cũ
                                if text != last_text or is_final != state.get("is_final", False):
                                    self._emit_result("transcripts", {
                                        "type": "transcripts",
                                        "text": text,
                                        "is_final": False,
                                        "session_id": session_id,
                                        "client_id": client_id
                                    }, worker_index)
                                    self.queue_translation(text, client_id, session_id, state, is_final=False)
                                else:
                                    logger.debug("[worker=%s] Skipped duplicate partial result for client=%s: '%s'", 
                                               worker_index, client_id, text)

                        # processed counter
                        with self._stats_lock:
                            self.worker_stats[worker_index]["processed"] += 1

            except queue.Empty:
                continue
            except Exception:
                logger.exception("[worker=%s] Fatal error in STT worker loop; continuing to run", worker_index)

    async def submit_audio_async(self, chunk, client_id, session_id):
        """Non-blocking submit with pre-VAD filtering and circuit breaker protection.

        Runs VAD and blocking queue.put in a thread to avoid blocking the event loop
        while preserving data (no drops).
        """
        import asyncio

        def _process_and_enqueue():
            try:
                # Use circuit breaker for audio processing
                return self._circuit_breaker.call(_process_audio_internal)
            except CircuitBreakerOpenException:
                logger.warning(f"STT circuit breaker is open for client {client_id}, dropping audio chunk")
                return False
            except Exception as e:
                logger.error(f"Unexpected error in STT processing for client {client_id}: {e}")
                return False
        
        def _process_audio_internal():
            # 1. Convert and validate audio
            try:
                audio_np = np.frombuffer(chunk, dtype=np.int16)
                if audio_np.size == 0:
                    logger.warning("Empty audio chunk received")
                    return False
                    
                audio_np = audio_np.astype(np.float32) / 32768.0
                
                # Validate audio data
                if np.any(np.isnan(audio_np)) or np.any(np.isinf(audio_np)):
                    logger.warning("Invalid audio values detected in chunk")
                    return False
                    
                # 2. Split into optimal chunks (512 samples) with 50% overlap
                chunk_size = 512  # Optimal size for 16kHz
                hop_length = chunk_size // 2  # 50% overlap
                
                # Pad if needed to ensure we have at least one full chunk
                if len(audio_np) < chunk_size:
                    pad_size = chunk_size - len(audio_np)
                    audio_np = np.pad(audio_np, (0, pad_size), mode='constant')
                
                # Create overlapping chunks
                chunks = []
                start = 0
                while start + chunk_size <= len(audio_np):
                    chunks.append(audio_np[start:start + chunk_size])
                    start += hop_length
                    
                if not chunks:
                    logger.warning("No valid chunks created from audio")
                    return False
                    
                logger.debug(
                    "Audio split into %d chunks of %d samples with %d overlap",
                    len(chunks), chunk_size, hop_length
                )
                
                # 2. Generate unique client identifier
                vad_client_id = f"{session_id}_{client_id}"
                
                # 3. Check if chunk size is optimal
                chunk_duration_ms = len(audio_np) * 1000 / self.config.audio.sample_rate
                if chunk_duration_ms < 20:  # Too short for reliable VAD
                    logger.warning("Chunk too short for reliable VAD: %.2f ms < 20ms", chunk_duration_ms)
                    return False
                
                if chunk_duration_ms > 500:  # Too long, might cause delays
                    logger.warning("Chunk too long, may cause processing delays: %.2f ms", chunk_duration_ms)
                
                # VAD removed - process all audio chunks directly
                logger.debug(
                    "Processing audio directly: client=%s session=%s | chunks=%d size=%d",
                    client_id, session_id, len(chunks), len(audio_np)
                )
                
                # Update metrics for monitoring (without VAD)
                with self._stats_lock:
                    s = self.worker_stats[hash(client_id) % self.num_workers]
                    s["last_chunk_duration_ms"] = chunk_duration_ms
                    s["total_chunks"] = len(chunks)
                    s["vad_filtered_chunks"] = len(chunks)  # All chunks are processed
                    
            except Exception as e:
                logger.error("Audio processing failed for client=%s session=%s: %s", 
                           client_id, session_id, str(e), exc_info=True)
                return False
            # Process all chunks (VAD removed)

            # Route to worker by client_id hash
            worker_index = hash(client_id) % self.num_workers
            worker_queue = self.worker_queues[worker_index]
            
            # If queue is getting full, try to clear old items
            current_size = worker_queue.qsize()
            if current_size >= self.config.queue.audio_queue_maxsize * 0.8:  # 80% full
                logger.warning(
                    "Queue %d filling up (%d/%d). Attempting cleanup...",
                    worker_index, current_size, self.config.queue.audio_queue_maxsize
                )
                
                # Try to remove old items
                items_to_clear = int(self.config.queue.audio_queue_maxsize * 0.2)  # Clear 20%
                dropped_items = 0
                for _ in range(items_to_clear):
                    try:
                        old_time, _, old_client, _ = worker_queue.get_nowait()
                        dropped_items += 1
                        logger.debug(
                            "Dropped old chunk from client %s (age: %.1fs)",
                            old_client, time.time() - old_time
                        )
                    except queue.Empty:
                        break
                
                if dropped_items > 0:
                    logger.info(
                        "Cleaned up %d old items from queue %d. New size: %d/%d",
                        dropped_items, worker_index, worker_queue.qsize(), self.config.queue.audio_queue_maxsize
                    )
            
            # After cleanup, check if still full
            if worker_queue.qsize() >= self.config.queue.audio_queue_maxsize:
                logger.error(
                    "Queue still full for worker %d (client=%s). Size=%d/%d",
                    worker_index, client_id, worker_queue.qsize(), self.config.queue.audio_queue_maxsize
                )
                return False  # Drop chunk if queue is still full
            
            # Enqueue new chunk
            worker_queue.put((time.time(), chunk, client_id, session_id))
            logger.debug("Enqueued audio for client=%s session=%s to worker=%s", client_id, session_id, worker_index)
            return True

        try:
            accepted = await asyncio.to_thread(_process_and_enqueue)
            if not accepted:
                logger.debug("Silence filtered for client=%s session=%s", client_id, session_id)
        except Exception:
            logger.exception("Error during async audio submit for client=%s session=%s", client_id, session_id)
            return False
        return True

    def queue_translation(self, text, client_id, session_id, state, is_final):
        task = {
            "text": text,
            "is_final": is_final,
            "session_id": session_id,
            "client_id": client_id
        }
        # self.audio_task_queue.put(task)
        state["last_queued_text"] = text
        state
        logger.info(f"[VOSK-{'FINAL' if is_final else 'PARTIAL'}] Queued for translation from {client_id}: '{text}'")

    def submit_audio(self, chunk, client_id, session_id):
        """Legacy synchronous submit (may block if queue is full). Prefer submit_audio_async."""
        worker_index = hash(client_id) % self.num_workers
        self.worker_queues[worker_index].put((time.time(), chunk, client_id, session_id))

    def get_result_nowait(self):
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self):
        """Clean shutdown of the service with resource cleanup."""
        try:
            logger.info("Initiating STTVoskService shutdown...")
            
            # 1. Stop accepting new audio
            self.stop_event.set()
            logger.info("Stop event set, waiting for workers to finish...")
            
            # 2. Process remaining queue items
            remaining_items = sum(q.qsize() for q in self.worker_queues)
            if remaining_items > 0:
                logger.info("Processing %d remaining items...", remaining_items)
            
            # 3. Wait for workers with timeout
            for i, t in enumerate(self.worker_threads):
                try:
                    t.join(timeout=5.0)
                    if t.is_alive():
                        logger.warning("Worker %d did not shut down gracefully", i)
                        # Could force stop here if needed
                except Exception as e:
                    logger.error("Error waiting for worker %d: %s", i, str(e))
            
            # 3.5. Wait for cleanup thread
            if hasattr(self, 'cleanup_thread'):
                try:
                    self.cleanup_thread.join(timeout=2.0)
                    if self.cleanup_thread.is_alive():
                        logger.warning("Cleanup thread did not shut down gracefully")
                except Exception as e:
                    logger.error("Error waiting for cleanup thread: %s", str(e))
            
            # Clean up worker resources and log final states (VAD removed)
            for i, (recognizers, states, queue) in enumerate(zip(
                self.worker_recognizers, 
                self.worker_client_state,
                self.worker_queues
            )):
                try:
                    # Log final worker state
                    logger.info(
                        "Worker %d final state: clients=%d queue=%d",
                        i, len(recognizers), queue.qsize()
                    )
                    # Clean up
                    recognizers.clear()
                    states.clear()
                    while not queue.empty():
                        try:
                            queue.get_nowait()
                        except:
                            pass
                except Exception as e:
                    logger.error("Error cleaning up worker %d: %s", i, str(e))
            
            # 6. Log final metrics
            with self._stats_lock:
                for i, stats in enumerate(self.worker_stats):
                    logger.info(
                        "Worker %d stats: processed=%d filtered=%d wait_ms=%.1f",
                        i,
                        stats.get("processed", 0),
                        stats.get("vad_filtered_chunks", 0),
                        stats.get("avg_wait_ms", 0)
                    )
            
            logger.info("STTVoskService shutdown completed successfully")
            
        except Exception as e:
            logger.error("Error during shutdown: %s", str(e), exc_info=True)
            raise  # Re-raise to ensure calling code knows shutdown failed

    def _metrics_loop(self):
        # Periodically log queue sizes, avg wait, processed, active clients
        while not self.stop_event.is_set():
            try:
                total_q = 0
                per_q = []
                for q in self.worker_queues:
                    size = q.qsize()
                    total_q += size
                    per_q.append(size)

                with self._stats_lock:
                    avg_waits = [round(s.get("avg_wait_ms", 0.0), 1) for s in self.worker_stats]
                    processed = [s.get("processed", 0) for s in self.worker_stats]

                active_clients = sum(len(m) for m in self.worker_recognizers)

                # Calculate VAD efficiency
                vad_filtered = sum(s.get("vad_filtered_chunks", 0) for s in self.worker_stats)
                total_chunks = sum(s.get("total_chunks", 0) for s in self.worker_stats)
                vad_efficiency = (vad_filtered / total_chunks * 100) if total_chunks > 0 else 0
                
                # Get latest chunk metrics
                latest_durations = [round(s.get("last_chunk_duration_ms", 0), 1) for s in self.worker_stats]
                latest_probs = [round(s.get("last_speech_prob", 0), 3) for s in self.worker_stats]
                
                logger.info(
                    "Metrics | System: workers=%s active_clients=%s | "
                    "Queue: total=%s per_worker=%s | "
                    "Performance: avg_wait_ms=%s processed=%s | "
                    "VAD: efficiency=%.1f%% chunks=%s/%s | "
                    "Latest: durations_ms=%s probs=%s",
                    self.num_workers, active_clients,
                    total_q, per_q,
                    avg_waits, processed,
                    vad_efficiency, vad_filtered, total_chunks,
                    latest_durations, latest_probs
                )
            except Exception:
                logger.exception("Metrics loop error")

            time.sleep(max(1.0, self.config.stt.metrics_interval_sec))

    def _cleanup_loop(self):
        """Periodic cleanup of resources to prevent memory leaks."""
        while not self.stop_event.is_set():
            try:
                current_time = time.time()
                cleanup_count = 0
                
                # Cleanup old accumulated chunks
                with self._accumulated_chunks_lock:
                    clients_to_remove = []
                    for client_id, (chunks, _, _, last_time) in self.accumulated_chunks.items():
                        if current_time - last_time > self.config.stt.max_accumulated_chunks_age:
                            clients_to_remove.append(client_id)
                    
                    for client_id in clients_to_remove:
                        del self.accumulated_chunks[client_id]
                        cleanup_count += 1
                
                # Cleanup old recognizers and states
                with self._client_states_lock:
                    for worker_index in range(self.num_workers):
                        recognizers = self.worker_recognizers[worker_index]
                        states = self.worker_client_state[worker_index]
                        
                        # Remove old client states (no activity for 5 minutes)
                        keys_to_remove = []
                        for key, state in states.items():
                            if current_time - state.get("last_translation_time", 0) > 300:
                                keys_to_remove.append(key)
                        
                        for key in keys_to_remove:
                            recognizers.pop(key, None)
                            states.pop(key, None)
                            cleanup_count += 1
                
                if cleanup_count > 0:
                    logger.info("Cleanup completed: removed %d inactive resources", cleanup_count)
                    
            except Exception as e:
                logger.error("Error in cleanup loop: %s", str(e))
            
            time.sleep(self.config.stt.client_cleanup_interval)


stt_service_vosk = STTVoskService()
