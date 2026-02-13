from typing import Optional, Type, Any
import traceback
import time
from dataclasses import dataclass
from enum import Enum

class ErrorSeverity(Enum):
    """Error severity levels"""
    LOW = "low"           # Minor issues, can continue
    MEDIUM = "medium"     # Significant issues, might need attention
    HIGH = "high"         # Serious issues, needs immediate attention
    CRITICAL = "critical" # System-breaking issues

@dataclass
class ErrorContext:
    """Context information for errors"""
    timestamp: float
    component: str
    operation: str
    severity: ErrorSeverity
    details: dict
    stack_trace: str
    
    @classmethod
    def create(cls, 
               component: str,
               operation: str,
               severity: ErrorSeverity,
               details: dict = None,
               include_stack: bool = True) -> 'ErrorContext':
        """Create error context with current timestamp"""
        return cls(
            timestamp=time.time(),
            component=component,
            operation=operation,
            severity=severity,
            details=details or {},
            stack_trace=traceback.format_exc() if include_stack else ""
        )

class AgentError(Exception):
    """Base exception for all agent errors"""
    def __init__(self, message: str, context: ErrorContext):
        super().__init__(message)
        self.context = context

class AudioProcessingError(AgentError):
    """Raised when there are errors in audio processing"""
    pass

class VADError(AudioProcessingError):
    """Raised when there are errors in Voice Activity Detection"""
    pass

class BufferError(AgentError):
    """Raised when there are buffer-related errors"""
    pass

class ResourceError(AgentError):
    """Raised when there are resource management errors"""
    pass

class ConfigurationError(AgentError):
    """Raised when there are configuration errors"""
    pass

class WebSocketError(AgentError):
    """Raised when there are WebSocket-related errors"""
    pass

class ErrorHandler:
    """Centralized error handling with retry capabilities"""
    
    def __init__(self):
        self._error_counts = {}
        self._last_errors = {}
    
    def handle(self, error: AgentError, max_retries: int = 3) -> bool:
        """
        Handle an error with potential retry logic.
        Returns True if the error was handled, False if it needs to be propagated.
        """
        component = error.context.component
        
        # Update error tracking
        self._error_counts[component] = self._error_counts.get(component, 0) + 1
        self._last_errors[component] = error
        
        # Handle based on severity
        if error.context.severity == ErrorSeverity.LOW:
            # Log and continue
            return True
            
        elif error.context.severity == ErrorSeverity.MEDIUM:
            # Check retry count
            if self._error_counts[component] <= max_retries:
                return True
            return False
            
        elif error.context.severity == ErrorSeverity.HIGH:
            # Always propagate high severity errors
            return False
            
        elif error.context.severity == ErrorSeverity.CRITICAL:
            # Always propagate critical errors
            return False
        
        return False
    
    def reset_count(self, component: str):
        """Reset error count for a component"""
        self._error_counts[component] = 0
    
    def get_last_error(self, component: str) -> Optional[AgentError]:
        """Get the last error for a component"""
        return self._last_errors.get(component)
    
    def get_error_count(self, component: str) -> int:
        """Get error count for a component"""
        return self._error_counts.get(component, 0)

def with_error_handling(component: str, 
                       operation: str, 
                       severity: ErrorSeverity = ErrorSeverity.MEDIUM,
                       error_type: Type[AgentError] = AgentError,
                       include_stack: bool = True):
    """
    Decorator for error handling with context
    
    Example:
    @with_error_handling("AudioProcessor", "process_chunk", ErrorSeverity.HIGH)
    def process_audio_chunk(self, chunk):
        # Processing logic here
    """
    def decorator(func):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                context = ErrorContext.create(
                    component=component,
                    operation=operation,
                    severity=severity,
                    details={"args": str(args), "kwargs": str(kwargs)},
                    include_stack=include_stack
                )
                raise error_type(str(e), context) from e
        return wrapper
    return decorator
