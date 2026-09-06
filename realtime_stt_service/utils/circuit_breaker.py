"""
Circuit Breaker Pattern implementation for robust error handling.

Modified version: Instead of OPEN/HALF_OPEN states that block requests,
this version disconnects and cleans up the client when threshold is reached.
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
    ACTIVE = "ACTIVE"          # Normal operation, monitoring failures
    DISCONNECTING = "DISCONNECTING"  # Threshold reached, disconnecting client


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""
    failure_threshold: int = 5  # Number of failures before disconnecting client
    expected_exception: type = Exception


class CircuitBreaker:
    """
    Circuit breaker pattern implementation for fault tolerance.
    
    Modified behavior:
    - ACTIVE: Normal operation, monitoring failures
    - When failure_threshold is reached: Disconnect client immediately
    - Client must reconnect to get a fresh circuit breaker
    
    This prevents cascading failures by removing problematic clients quickly.
    """
    
    def __init__(self, config: CircuitBreakerConfig, on_threshold_reached=None):
        self.config = config
        self.state = CircuitState.ACTIVE
        self.failure_count = 0
        self.last_failure_time = None
        self._lock = threading.RLock()
        self._on_threshold_reached = on_threshold_reached
        self._disconnecting = False  # Track if disconnect initiated
        
        logger.info(
            f"Circuit breaker initialized: failure_threshold={config.failure_threshold} "
            f"(will disconnect client when threshold reached)"
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
            CircuitBreakerOpenException: When client is being disconnected
            Exception: Original function exception
        """
        with self._lock:
            # If already disconnecting, reject all calls
            if self.state == CircuitState.DISCONNECTING or self._disconnecting:
                raise CircuitBreakerOpenException(
                    f"Circuit breaker is DISCONNECTING client. "
                    f"Failure threshold reached: {self.failure_count}/{self.config.failure_threshold}"
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
    
    def can_try(self) -> bool:
        """
        Check if circuit breaker allows calls.
        
        Returns False when client is being disconnected.
        """
        with self._lock:
            return self.state == CircuitState.ACTIVE and not self._disconnecting
    
    def _on_success(self):
        """Handle successful call."""
        if self.state == CircuitState.ACTIVE:
            # Reset failure count on success
            if self.failure_count > 0:
                logger.debug(f"Circuit breaker success: resetting failure count from {self.failure_count} to 0")
                self.failure_count = 0
    
    def _on_failure(self):
        """Handle failed call - disconnect client when threshold reached."""
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        logger.warning(
            f"Circuit breaker recorded failure #{self.failure_count}/{self.config.failure_threshold} "
            f"(state: {self.state.value})"
        )
        
        # Check if threshold reached
        if self.failure_count >= self.config.failure_threshold:
            self.state = CircuitState.DISCONNECTING
            self._disconnecting = True
            
            logger.error(
                f"🚨 CIRCUIT BREAKER THRESHOLD REACHED - DISCONNECTING CLIENT! 🚨\n"
                f"   Reason: Exceeded failure threshold ({self.failure_count}/{self.config.failure_threshold})\n"
                f"   Last failure time: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(self.last_failure_time))}\n"
                f"   Action: Client will be disconnected and cleaned up immediately\n"
                f"   Recovery: Client must reconnect to get a fresh start"
            )
            
            # Trigger disconnect callback
            if self._on_threshold_reached:
                try:
                    self._on_threshold_reached()
                except Exception as e:
                    logger.error(f"Error calling disconnect callback: {e}", exc_info=True)
        else:
            remaining_failures = self.config.failure_threshold - self.failure_count
            logger.warning(
                f"Circuit breaker approaching threshold: {self.failure_count}/{self.config.failure_threshold} "
                f"({remaining_failures} more failures will trigger client disconnect)"
            )
    
    def get_state(self) -> dict:
        """Get current circuit breaker state.
        
        Returns:
            dict: Current state containing:
                - state: Current circuit state (ACTIVE or DISCONNECTING)
                - failure_count: Number of consecutive failures
                - last_failure_time: Timestamp of last failure (or None)
                - time_since_last_failure: Seconds since last failure (or None)
                - disconnecting: Whether client is being disconnected
        """
        with self._lock:
            return {
                'state': self.state.value,
                'failure_count': self.failure_count,
                'last_failure_time': self.last_failure_time,
                'time_since_last_failure': (
                    time.time() - self.last_failure_time 
                    if self.last_failure_time else None
                ),
                'disconnecting': self._disconnecting
            }
    
    def reset(self):
        """Manually reset circuit breaker to ACTIVE state.
        
        Note: This is typically called when a client reconnects.
        """
        with self._lock:
            self.state = CircuitState.ACTIVE
            self.failure_count = 0
            self.last_failure_time = None
            self._disconnecting = False
            logger.info("Circuit breaker manually reset to ACTIVE state")


class CircuitBreakerOpenException(Exception):
    """Exception raised when circuit breaker is open."""
    pass


# Global circuit breaker for STT service
_stt_circuit_breaker: Optional[CircuitBreaker] = None


def get_stt_circuit_breaker() -> CircuitBreaker:
    """Get or create STT circuit breaker.
    
    Note: This returns a global circuit breaker. In practice, each client
    should have its own circuit breaker instance for isolated failure tracking.
    """
    global _stt_circuit_breaker
    if _stt_circuit_breaker is None:
        try:
            from ..config import get_config
            app_config = get_config()
            config = CircuitBreakerConfig(
                failure_threshold=app_config.circuit_breaker.stt_failure_threshold,
                expected_exception=Exception
            )
        except ImportError:
            # Fallback to default config if config system not available
            config = CircuitBreakerConfig(
                failure_threshold=5,
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
