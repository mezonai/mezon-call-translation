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
    timeout: float = 10.0
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
        self.last_reset_attempt_time = None  # Track when we last attempted reset
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
    
    def record_failure(self):
        """Manually record a failure."""
        with self._lock:
            self._on_failure()
    
    def record_success(self):
        """Manually record a success."""
        with self._lock:
            self._on_success()
    
    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        if self.last_failure_time is None:
            return True
        return time.time() - self.last_failure_time >= self.config.timeout
    
    def can_try(self) -> bool:
        """Check if circuit breaker allows calls."""
        with self._lock:
            current_time = time.time()
            
            if self.state == CircuitState.CLOSED:
                return True
            elif self.state == CircuitState.HALF_OPEN:
                return True
            elif self.state == CircuitState.OPEN:
                time_since_failure = current_time - self.last_failure_time if self.last_failure_time else 0
                can_reset = self._should_attempt_reset()
                
                if can_reset:
                    # Only log transition message once per reset attempt
                    should_log_transition = (
                        self.last_reset_attempt_time is None or 
                        current_time - self.last_reset_attempt_time > 5.0  # Log at most every 5 seconds
                    )
                    
                    if should_log_transition:
                        logger.info(
                            f"Circuit breaker transitioning from OPEN to HALF_OPEN after {time_since_failure:.1f}s "
                            f"(timeout: {self.config.timeout}s, failures: {self.failure_count})"
                        )
                        self.last_reset_attempt_time = current_time
                    
                    self.state = CircuitState.HALF_OPEN
                    self.success_count = 0  # Reset success count for HALF_OPEN state
                    return True
                else:
                    # Only log blocking message occasionally to avoid spam
                    if self.last_reset_attempt_time is None or current_time - self.last_reset_attempt_time > 10.0:
                        logger.debug(
                            f"Circuit breaker OPEN - blocking calls. Time since failure: {time_since_failure:.1f}s, "
                            f"timeout needed: {self.config.timeout}s, failure count: {self.failure_count}/{self.config.failure_threshold}"
                        )
                        self.last_reset_attempt_time = current_time
                    return False
            return False
    
    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.HALF_OPEN:
            self.success_count += 1
            logger.info(
                f"Circuit breaker success in HALF_OPEN: {self.success_count}/{self.config.success_threshold} "
                f"(need {self.config.success_threshold - self.success_count} more to close)"
            )
            if self.success_count >= self.config.success_threshold:
                self.state = CircuitState.CLOSED
                self.failure_count = 0
                self.success_count = 0
                logger.info(
                    f"✅ CIRCUIT BREAKER FULLY RECOVERED! ✅\n"
                    f"   State changed from HALF_OPEN to CLOSED\n"
                    f"   Service is now fully operational\n"
                    f"   Failure count reset to 0"
                )
        elif self.state == CircuitState.CLOSED:
            # Reset failure count on success in normal state
            if self.failure_count > 0:
                logger.debug(f"Circuit breaker success: resetting failure count from {self.failure_count} to 0")
                self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed call."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        logger.warning(
            f"Circuit breaker recorded failure #{self.failure_count} "
            f"(threshold: {self.config.failure_threshold}, state: {self.state.value})"
        )
        
        if self.state == CircuitState.HALF_OPEN:
            # Failed in half-open state, go back to open
            self.state = CircuitState.OPEN
            self.success_count = 0
            logger.error(
                f"Circuit breaker FAILED in HALF_OPEN state after {self.success_count} successes, "
                f"returning to OPEN state. Total failures: {self.failure_count}"
            )
        elif self.state == CircuitState.CLOSED:
            if self.failure_count >= self.config.failure_threshold:
                self.state = CircuitState.OPEN
                logger.error(
                    f"🚨 CIRCUIT BREAKER OPENED! 🚨\n"
                    f"   Reason: Exceeded failure threshold ({self.failure_count}/{self.config.failure_threshold})\n"
                    f"   Will block all requests for {self.config.timeout} seconds\n"
                    f"   Last failure time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_failure_time))}\n"
                    f"   Recovery: Need {self.config.success_threshold} consecutive successes to fully recover"
                )
            else:
                remaining_failures = self.config.failure_threshold - self.failure_count
                logger.warning(
                    f"Circuit breaker approaching threshold: {self.failure_count}/{self.config.failure_threshold} "
                    f"({remaining_failures} more failures will open circuit)"
                )
    
    def get_state(self) -> dict:
        """Get current circuit breaker state."""
        with self._lock:
            return {
                'state': self.state.value,
                'failure_count': self.failure_count,
                'success_count': self.success_count,
                'last_failure_time': self.last_failure_time,
                'last_reset_attempt_time': self.last_reset_attempt_time,
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
            self.last_reset_attempt_time = None
            logger.info("Circuit breaker manually reset to CLOSED state")


class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open."""
    pass


# Global circuit breaker for STT service
_stt_circuit_breaker: Optional[CircuitBreaker] = None


def get_stt_circuit_breaker() -> CircuitBreaker:
    """Get or create STT circuit breaker."""
    global _stt_circuit_breaker
    if _stt_circuit_breaker is None:
        try:
            from ..config import get_config
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
    global _stt_circuit_breaker
    
    if _stt_circuit_breaker:
        _stt_circuit_breaker.reset()
    
    logger.info("All circuit breakers reset")


def get_all_circuit_breaker_states() -> dict:
    """Get states of all circuit breakers."""
    states = {}
    
    if _stt_circuit_breaker:
        states['stt'] = _stt_circuit_breaker.get_state()
    
    return states
