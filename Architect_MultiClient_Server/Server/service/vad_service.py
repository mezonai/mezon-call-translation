"""
VAD Service - Centralized Voice Activity Detection service with improved resource management.
"""
import torch
import numpy as np
from typing import Optional, Dict, Any
import time
import logging
import threading
from dataclasses import dataclass
from utils.circuit_breaker import get_vad_circuit_breaker, CircuitBreakerOpenException
from service.health_service import register_vad_health_checks
from config import get_config

logger = logging.getLogger(__name__)


# VADConfig is now imported from config package


class VADService:
    """Thread-safe VAD service with improved resource management."""
    
    def __init__(self, config=None):
        self.config = config or get_config().vad
        self._client_states_lock = threading.RLock()
        self._client_states: Dict[str, Dict[str, Any]] = {}
        self._stop_event = threading.Event()
        self._vad_disabled = False  # Flag to disable VAD if it keeps failing
        
        # Initialize circuit breaker
        self._circuit_breaker = get_vad_circuit_breaker()
        
        # Initialize Silero VAD
        try:
            self._init_vad()
        except Exception as e:
            logger.error(f"Failed to initialize VAD, disabling VAD functionality: {e}")
            self._vad_disabled = True
        
        # Start cleanup thread
        self._cleanup_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleanup_thread.start()
        
        # Register health checks
        register_vad_health_checks(self)
        
        logger.info("VAD Service initialized successfully")
    
    def _init_vad(self):
        """Initialize Silero VAD model."""
        try:
            # Set number of threads for optimization
            torch.set_num_threads(1)
            
            logger.info("Loading Silero VAD model...")
            
            # Load model with caching
            self.model, self.utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                trust_repo=True,
                force_reload=False
            )
            (self.get_speech_ts,
             self.save_audio,
             self.read_audio,
             self.VADIterator,
             self.collect_chunks) = self.utils
            
            # Configure device
            self.device = self.config.device or ('cuda' if torch.cuda.is_available() else 'cpu')
            self.model.to(self.device)
            
            if self.device == 'cuda':
                self.model = self.model.half()  # FP16 for GPU
            self.model.eval()
            
            # Test VAD with dummy data to ensure it works
            logger.info("Testing VAD with dummy data...")
            test_audio = torch.zeros(512, dtype=torch.float32).to(self.device)
            test_iterator = self.VADIterator(self.model)
            test_result = test_iterator(test_audio, get_config().audio.sample_rate)
            
            if test_result is not None:
                logger.info(f"Silero VAD loaded successfully on device: {self.device}, test result: {test_result}")
            else:
                logger.warning("VAD test returned None - this may cause issues")
            
        except Exception as e:
            logger.error(f"Failed to initialize Silero VAD: {e}")
            raise
    
    def _init_client_state(self, client_id: str) -> None:
        """Initialize state for a new client."""
        if client_id not in self._client_states:
            try:
                self._client_states[client_id] = {
                    'vad_iterator': self.VADIterator(self.model),
                    'speech_timestamps': [],
                    'last_speech_timestamp': None,
                    'last_activity': time.time(),
                    'vad_error_count': 0,
                    'stats': {
                        'chunks_processed': 0,
                        'speech_detected': 0,
                        'avg_probability': 0.0,
                        'total_duration_ms': 0
                    }
                }
                logger.debug(f"Initialized VAD state for client {client_id}")
            except Exception as e:
                logger.error(f"Failed to initialize VAD state for client {client_id}: {e}")
                raise
    
    def _get_client_state(self, client_id: str) -> Dict[str, Any]:
        """Get client state, initializing if needed."""
        with self._client_states_lock:
            self._init_client_state(client_id)
            self._client_states[client_id]['last_activity'] = time.time()
            return self._client_states[client_id]
    
    def is_speech(self, audio: np.ndarray, client_id: str = "default") -> bool:
        """
        Detect speech in audio using Silero VAD with circuit breaker protection.
        
        Args:
            audio: 1D numpy float32 array normalized to [-1, 1]
            client_id: Unique identifier for the client
            
        Returns:
            True if speech detected, False for silence
        """
        # If VAD is disabled, use simple energy-based detection
        if self._vad_disabled:
            logger.debug(f"VAD disabled, using energy-based detection for client {client_id}")
            return self._simple_energy_detection(audio)
        
        try:
            # Use circuit breaker for VAD processing
            return self._circuit_breaker.call(self._process_speech_detection, audio, client_id)
        except CircuitBreakerOpenException:
            logger.warning(f"VAD circuit breaker is open for client {client_id}, using fallback detection")
            return self._simple_energy_detection(audio)
        except Exception as e:
            logger.error(f"Unexpected error in VAD processing for client {client_id}: {e}")
            return self._simple_energy_detection(audio)
    
    def _process_speech_detection(self, audio: np.ndarray, client_id: str) -> bool:
        """
        Internal method for speech detection processing.
        
        Args:
            audio: 1D numpy float32 array normalized to [-1, 1]
            client_id: Unique identifier for the client
            
        Returns:
            True if speech detected, False for silence
        """
        try:
            # Input validation
            if not isinstance(audio, np.ndarray):
                logger.warning(f"Invalid audio type for client {client_id}")
                return False
            
            if len(audio) == 0:
                logger.warning(f"Empty audio for client {client_id}")
                return False
            
            # Preprocess audio
            if audio.dtype != np.float32:
                audio = audio.astype(np.float32)
            if np.abs(audio).max() > 1:
                audio = audio / np.abs(audio).max()
            
            # Get client state
            state = self._get_client_state(client_id)
            
            # Split into chunks with overlap
            chunk_size = self.config.window_size_samples
            hop_length = chunk_size // 2  # 50% overlap
            
            # Pad if needed
            if len(audio) < chunk_size:
                pad_size = chunk_size - len(audio)
                audio = np.pad(audio, (0, pad_size), mode='constant')
            
            # Create overlapping chunks
            chunks = []
            start = 0
            while start + chunk_size <= len(audio):
                chunks.append(audio[start:start + chunk_size])
                start += hop_length
            
            if not chunks:
                logger.warning(f"No valid chunks created for client {client_id}")
                return False
            
            # Process chunks
            speech_probs = []
            current_time = time.time()
            
            with torch.no_grad():
                for i, chunk_audio in enumerate(chunks):
                    try:
                        # Convert to tensor
                        audio_tensor = torch.from_numpy(chunk_audio).to(self.device)
                        if self.device == 'cuda':
                            audio_tensor = audio_tensor.half()
                        
                        # Process through VAD
                        vad_result = state['vad_iterator'](audio_tensor, get_config().audio.sample_rate)
                        
                        # Handle different return types and None values
                        if vad_result is None:
                            logger.warning(f"VAD returned None for chunk {i+1}/{len(chunks)} - client {client_id}")
                            state['vad_error_count'] = state.get('vad_error_count', 0) + 1
                            
                            # Reset VAD iterator if too many errors
                            if state['vad_error_count'] > 3:  # Reduced threshold for faster recovery
                                logger.info(f"Resetting VAD iterator for client {client_id} due to {state['vad_error_count']} consecutive None results")
                                try:
                                    state['vad_iterator'] = self.VADIterator(self.model)
                                    state['vad_error_count'] = 0
                                except Exception as reset_error:
                                    logger.error(f"Failed to reset VAD iterator for client {client_id}: {reset_error}")
                            continue
                        
                        if isinstance(vad_result, dict):
                            prob = float(vad_result.get('probability', 0.0))
                        else:
                            prob = float(vad_result)
                        
                        # Reset error count on successful processing
                        state['vad_error_count'] = 0
                        speech_probs.append(prob)
                        
                        logger.debug(
                            f"Chunk {i+1}/{len(chunks)}: prob={prob:.3f} rms={np.sqrt(np.mean(np.square(chunk_audio))):.3f}"
                        )
                        
                    except Exception as e:
                        logger.error(f"Error processing chunk {i+1}/{len(chunks)}: {e}")
                        state['vad_error_count'] = state.get('vad_error_count', 0) + 1
                        
                        # Reset VAD iterator if too many errors
                        if state['vad_error_count'] > 5:
                            logger.info(f"Resetting VAD iterator for client {client_id} due to {state['vad_error_count']} errors")
                            state['vad_iterator'] = self.VADIterator(self.model)
                            state['vad_error_count'] = 0
                        continue
            
            # Analyze results
            if not speech_probs:
                logger.warning(f"No valid chunks processed for client {client_id} - using fallback detection")
                # Fallback: use RMS energy as simple voice activity detection
                rms_values = []
                for chunk_audio in chunks:
                    rms = np.sqrt(np.mean(np.square(chunk_audio)))
                    rms_values.append(rms)
                
                if rms_values:
                    avg_rms = np.mean(rms_values)
                    # Simple threshold-based detection (adjust as needed)
                    speech_detected = avg_rms > 0.01  # Very low threshold for fallback
                    logger.debug(f"Fallback VAD for client {client_id}: avg_rms={avg_rms:.4f}, speech={speech_detected}")
                    return speech_detected
                else:
                    return False
            
            max_prob = max(speech_probs)
            avg_prob = sum(speech_probs) / len(speech_probs)
            speech_detected = max_prob > self.config.threshold
            
            # Update state
            duration_ms = len(audio) * 1000 / get_config().audio.sample_rate
            
            if speech_detected:
                if not state['last_speech_timestamp']:
                    state['last_speech_timestamp'] = current_time
                state['speech_timestamps'].append({
                    'start': current_time,
                    'probability': max_prob,
                    'duration_ms': duration_ms
                })
            elif state['last_speech_timestamp']:
                silence_duration = current_time - state['last_speech_timestamp']
                if silence_duration * 1000 >= self.config.min_silence_duration_ms:
                    state['last_speech_timestamp'] = None
            
            # Update statistics
            stats = state['stats']
            stats['chunks_processed'] += len(chunks)
            if speech_detected:
                stats['speech_detected'] += 1
            stats['avg_probability'] = stats['avg_probability'] * 0.95 + avg_prob * 0.05
            stats['total_duration_ms'] += duration_ms
            
            # Cleanup old timestamps
            state['speech_timestamps'] = [
                ts for ts in state['speech_timestamps']
                if current_time - ts['start'] <= self.config.min_speech_duration_ms / 1000
            ]
            
            logger.debug(
                f"VAD result for {client_id}: speech={speech_detected} prob={max_prob:.3f}/{avg_prob:.3f} "
                f"chunks={len(chunks)} duration={duration_ms:.1f}ms"
            )
            
            return speech_detected
            
        except Exception as e:
            logger.error(f"Error processing audio for client {client_id}: {e}", exc_info=True)
            return False
    
    def _simple_energy_detection(self, audio: np.ndarray) -> bool:
        """
        Simple energy-based voice activity detection as fallback.
        
        Args:
            audio: 1D numpy array
            
        Returns:
            True if energy is above threshold, False otherwise
        """
        try:
            # Calculate RMS energy
            rms = np.sqrt(np.mean(np.square(audio)))
            
            # Simple threshold (adjust as needed)
            threshold = 0.01  # Very low threshold to catch most speech
            
            speech_detected = rms > threshold
            logger.debug(f"Simple energy detection: rms={rms:.4f}, threshold={threshold}, speech={speech_detected}")
            
            return speech_detected
            
        except Exception as e:
            logger.error(f"Error in simple energy detection: {e}")
            return False
    
    def get_speech_prob(self, client_id: str = "default") -> Optional[float]:
        """Get latest speech probability for a client."""
        with self._client_states_lock:
            state = self._client_states.get(client_id)
            if state and state['speech_timestamps']:
                return state['speech_timestamps'][-1]['probability']
            return None
    
    def get_client_stats(self, client_id: str = "default") -> Dict[str, Any]:
        """Get detailed stats for a client."""
        with self._client_states_lock:
            state = self._client_states.get(client_id)
            if not state:
                return {}
            
            current_time = time.time()
            recent_timestamps = [
                ts for ts in state['speech_timestamps']
                if current_time - ts['start'] <= 5  # Last 5 seconds
            ]
            
            stats = state['stats']
            return {
                'active': bool(state['last_speech_timestamp']),
                'last_activity': state['last_activity'],
                'speech_count': len(recent_timestamps),
                'avg_probability': stats['avg_probability'],
                'chunks_processed': stats['chunks_processed'],
                'speech_detected': stats['speech_detected'],
                'total_duration_s': stats['total_duration_ms'] / 1000,
                'detection_rate': (stats['speech_detected'] / stats['chunks_processed'] 
                                 if stats['chunks_processed'] > 0 else 0)
            }
    
    def reset_client(self, client_id: str) -> None:
        """Reset state for a specific client."""
        with self._client_states_lock:
            if client_id in self._client_states:
                stats = self.get_client_stats(client_id)
                logger.info(
                    f"Resetting VAD client {client_id} | Stats: duration={stats.get('total_duration_s', 0):.1f}s "
                    f"speech={stats.get('speech_detected', 0)}/{stats.get('chunks_processed', 0)} "
                    f"({stats.get('detection_rate', 0)*100:.1f}%) avg_prob={stats.get('avg_probability', 0):.3f}"
                )
                del self._client_states[client_id]
                self._init_client_state(client_id)
    
    def cleanup_inactive_clients(self, max_age: float = None) -> None:
        """Remove inactive client states."""
        max_age = max_age or self.config.max_client_idle_time
        current_time = time.time()
        
        with self._client_states_lock:
            inactive_clients = [
                client_id for client_id, state in self._client_states.items()
                if current_time - state['last_activity'] > max_age
            ]
            
            for client_id in inactive_clients:
                stats = self.get_client_stats(client_id)
                logger.info(
                    f"Cleaning up inactive VAD client {client_id} | Stats: processed={stats.get('chunks_processed', 0)} "
                    f"speech={stats.get('speech_detected', 0)} avg_prob={stats.get('avg_probability', 0):.3f} "
                    f"duration={stats.get('total_duration_s', 0):.1f}s"
                )
                del self._client_states[client_id]
    
    def _cleanup_loop(self):
        """Periodic cleanup of inactive clients."""
        while not self._stop_event.is_set():
            try:
                self.cleanup_inactive_clients()
            except Exception as e:
                logger.error(f"Error in VAD cleanup loop: {e}")
            
            self._stop_event.wait(self.config.cleanup_interval)
    
    def get_all_stats(self) -> Dict[str, Any]:
        """Get statistics for all clients."""
        with self._client_states_lock:
            total_clients = len(self._client_states)
            total_processed = sum(
                state['stats']['chunks_processed'] for state in self._client_states.values()
            )
            total_speech = sum(
                state['stats']['speech_detected'] for state in self._client_states.values()
            )
            total_duration = sum(
                state['stats']['total_duration_ms'] for state in self._client_states.values()
            )
            
            return {
                'total_clients': total_clients,
                'total_chunks_processed': total_processed,
                'total_speech_detected': total_speech,
                'total_duration_s': total_duration / 1000,
                'overall_detection_rate': (total_speech / total_processed if total_processed > 0 else 0),
                'avg_duration_per_client': (total_duration / total_clients / 1000 if total_clients > 0 else 0)
            }
    
    def shutdown(self):
        """Clean shutdown of VAD service."""
        try:
            logger.info("Shutting down VAD service...")
            
            # Stop cleanup thread
            self._stop_event.set()
            if hasattr(self, '_cleanup_thread'):
                self._cleanup_thread.join(timeout=2.0)
            
            # Cleanup all clients
            with self._client_states_lock:
                for client_id in list(self._client_states.keys()):
                    self.reset_client(client_id)
                self._client_states.clear()
            
            # Free GPU memory if needed
            if self.device == 'cuda':
                self.model.cpu()
                torch.cuda.empty_cache()
            
            logger.info("VAD service shutdown completed")
            
        except Exception as e:
            logger.error(f"Error during VAD service shutdown: {e}", exc_info=True)
            raise


# Global VAD service instance
_vad_service: Optional[VADService] = None


def get_vad_service() -> VADService:
    """Get or create global VAD service instance."""
    global _vad_service
    if _vad_service is None:
        _vad_service = VADService()
    return _vad_service


def shutdown_vad_service():
    """Shutdown global VAD service."""
    global _vad_service
    if _vad_service is not None:
        _vad_service.shutdown()
        _vad_service = None
