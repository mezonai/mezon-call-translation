"""
Circuit Breaker Pattern implementation for robust error handling.
"""
import time
import logging
from enum import Enum
from typing import Callable, Any, Optional
from dataclasses import dataclass
import threading

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "CLOSED"      # Normal operation
    OPEN = "OPEN"          # Circuit is open, calls fail fast
    HALF_OPEN = "HALF_OPEN"  # Testing if service is back


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5
    timeout: float = 60.0
    success_threshold: int = 3
    expected_exception: type = Exception


class CircuitBreaker:
    """
    Circuit breaker pattern implementation for fault tolerance.
    
    The circuit breaker has three states:
    - CLOSED: Normal operation, calls pass through
    - OPEN: Circuit is open, calls fail fast
    - HALF_OPEN: Testing if service is back, limited calls allowed
    """
    
    def __init__(self, config: CircuitBreakerConfig):
        self.config = config
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.success_count = 0
        self.last_failure_time = None
        self._lock = threading.RLock()
        
        logger.info(
            f"Circuit breaker initialized: failure_threshold={config.failure_threshold}, "
            f"timeout={config.timeout}s, success_threshold={config.success_threshold}"
        )
    
    def call(self, func: Callable, *args, **kwargs) -> Any:
        """
        Execute function with circuit breaker protection.
        
        Args:
            func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Function result
            
        Raises:
            CircuitBreakerOpenException: When circuit is open
            Exception: Original function exception
        """
        with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0
                    logger.info("Circuit breaker transitioning to HALF_OPEN state")
                else:
                    raise CircuitBreakerOpenException(
                        f"Circuit breaker is OPEN. Last failure: {self.last_failure_time}"
                    )
            
            try:
                result = func(*args, **kwargs)
                self._on_success()
                return result
                
            except self.config.expected_exception as e:
                self._on_failure()
                raise e
            except Exception as e:
                # Unexpected exception, don't count as failure
                logger.warning(f"Unexpected exception in circuit breaker: {e}")
                raise e
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.config.timeout
    
    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info("Circuit breaker reset to CLOSED state")
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success
            self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.HALF_OPEN:
            # Failed in half-open state, go back to open
            self.state = CircuitState.OPEN
            self.success_count = 0
            logger.warning("Circuit breaker failed in HALF_OPEN state, returning to OPEN")
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                logger.warning(
                    f"Circuit breaker opened after {self.failure_count} failures. "
                    f"Will retry after {self.config.timeout}s"
                )
    
    def get_state(self) -> dict:
        """Get current circuit breaker state."""
        with self._lock:
            return {
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'last_failure_time': self.last_failure_time,
                'time_since_last_failure': (
                    time.time() - self.last_failure_time 
                    if self.last_failure_time else None
                )
            }
    
    def reset(self):
        """Manually reset circuit breaker to CLOSED state."""
        with self._lock:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.success_count = 0
            self.last_failure_time = None
            logger.info("Circuit breaker manually reset to CLOSED state")


class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open."""
    pass


# Global circuit breakers for different services
_vad_circuit_breaker: Optional[CircuitBreaker] = None
_stt_circuit_breaker: Optional[CircuitBreaker] = None


def get_vad_circuit_breaker() -> CircuitBreaker:
    """Get or create VAD circuit breaker."""
    global _vad_circuit_breaker
    if _vad_circuit_breaker is None:
        try:
            from config import get_config
            app_config = get_config()
            config = CircuitBreakerConfig(
                failure_threshold=app_config.circuit_breaker.vad_failure_threshold,
                timeout=app_config.circuit_breaker.vad_timeout,
                success_threshold=app_config.circuit_breaker.vad_success_threshold,
                expected_exception=Exception
            )
        except ImportError:
            # Fallback to default config if config system not available
            config = CircuitBreakerConfig(
                failure_threshold=3,
                timeout=30.0,
                success_threshold=2,
                expected_exception=Exception
            )
        _vad_circuit_breaker = CircuitBreaker(config)
    return _vad_circuit_breaker


def get_stt_circuit_breaker() -> CircuitBreaker:
    """Get or create STT circuit breaker."""
    global _stt_circuit_breaker
    if _stt_circuit_breaker is None:
        try:
            from config import get_config
            app_config = get_config()
            config = CircuitBreakerConfig(
                failure_threshold=app_config.circuit_breaker.stt_failure_threshold,
                timeout=app_config.circuit_breaker.stt_timeout,
                success_threshold=app_config.circuit_breaker.stt_success_threshold,
                expected_exception=Exception
            )
        except ImportError:
            # Fallback to default config if config system not available
            config = CircuitBreakerConfig(
                failure_threshold=5,
                timeout=60.0,
                success_threshold=3,
                expected_exception=Exception
            )
        _stt_circuit_breaker = CircuitBreaker(config)
    return _stt_circuit_breaker


def reset_all_circuit_breakers():
    """Reset all circuit breakers."""
    global _vad_circuit_breaker, _stt_circuit_breaker
    
    if _vad_circuit_breaker:
        _vad_circuit_breaker.reset()
    if _stt_circuit_breaker:
        _stt_circuit_breaker.reset()
    
    logger.info("All circuit breakers reset")


def get_all_circuit_breaker_states() -> dict:
    """Get states of all circuit breakers."""
    states = {}
    
    if _vad_circuit_breaker:
        states['vad'] = _vad_circuit_breaker.get_state()
    if _stt_circuit_breaker:
        states['stt'] = _stt_circuit_breaker.get_state()
    
    return states
