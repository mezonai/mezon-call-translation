from enum import Enum
import time
from dataclasses import dataclass
from typing import Optional

@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5
    reset_timeout: float = 10.0
    half_open_timeout: float = 5.0

class CircuitState(Enum):
    CLOSED = "closed"      # Normal operation
    OPEN = "open"         # Circuit is broken
    HALF_OPEN = "half_open"  # Testing if service is back

class CircuitBreaker:
    """Circuit breaker pattern implementation"""
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0
        self.last_state_change = time.time()
    
    def record_failure(self):
        """Record a failure and possibly open the circuit"""
        self.failures += 1
        self.last_failure_time = time.time()
        
        if self.state == CircuitState.CLOSED and self.failures >= self.config.failure_threshold:
            self.state = CircuitState.OPEN
            self.last_state_change = time.time()
            return True
            
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.OPEN
            self.last_state_change = time.time()
            return True
            
        return False
    
    def record_success(self):
        """Record a success and possibly close the circuit"""
        self.failures = 0
        
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.last_state_change = time.time()
            return True
            
        return False
    
    def can_try(self) -> bool:
        """Check if we can try the operation"""
        now = time.time()
        
        if self.state == CircuitState.CLOSED:
            return True
            
        if self.state == CircuitState.OPEN:
            if now - self.last_state_change >= self.config.reset_timeout:
                self.state = CircuitState.HALF_OPEN
                self.last_state_change = now
                return True
            return False
            
        if self.state == CircuitState.HALF_OPEN:
            return now - self.last_state_change >= self.config.half_open_timeout
            
        return False
    
    def get_state(self) -> CircuitState:
        """Get current circuit state"""
        return self.state
    
    def reset(self):
        """Reset circuit breaker state"""
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.last_failure_time = 0
        self.last_state_change = time.time()
