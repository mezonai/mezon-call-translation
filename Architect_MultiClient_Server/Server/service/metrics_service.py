from prometheus_client import Counter, Gauge, Histogram, Summary
import time

class MetricsService:
    """Service for managing Prometheus metrics"""
    
    # WebSocket metrics
    ws_connections = Gauge('ws_connections_current', 'Current number of websocket connections')
    ws_messages_total = Counter('ws_messages_total', 'Total websocket messages', ['direction', 'session_id'])
    ws_errors = Counter('ws_errors_total', 'Total websocket errors', ['type'])
    
    # Speech Processing metrics
    speech_segments = Counter('speech_segments_total', 'Total speech segments detected')
    audio_processing_duration = Histogram(
        'audio_processing_seconds', 
        'Time spent processing audio',
        buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0)
    )
    
    # STT metrics
    transcription_requests = Counter('transcription_requests_total', 'Total transcription requests')
    transcription_errors = Counter('transcription_errors_total', 'Transcription errors', ['error_type'])
    transcription_duration = Histogram(
        'transcription_duration_seconds',
        'Time spent on transcription',
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
    )
    
    # Translation metrics
    translation_requests = Counter('translation_requests_total', 'Total translation requests')
    translation_errors = Counter('translation_errors_total', 'Translation errors', ['error_type'])
    translation_duration = Histogram(
        'translation_duration_seconds',
        'Time spent on translation',
        buckets=(0.1, 0.5, 1.0, 2.0, 5.0, 10.0)
    )
    
    # Performance metrics
    cpu_usage = Gauge('cpu_usage_percent', 'CPU usage percentage')
    memory_usage = Gauge('memory_usage_bytes', 'Memory usage in bytes')
    queue_size = Gauge('queue_size', 'Current queue size', ['queue_name'])
    
    # Circuit Breaker metrics
    circuit_breaker_state = Gauge('circuit_breaker_state', 'Circuit breaker state (0=closed, 1=half-open, 2=open)', ['name'])
    circuit_breaker_failures = Counter('circuit_breaker_failures_total', 'Circuit breaker failures', ['name'])
    
    # Request metrics
    http_requests_total = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
    http_request_duration = Histogram(
        'http_request_duration_seconds',
        'HTTP request duration in seconds',
        ['endpoint'],
        buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0, 10.0)
    )

    @classmethod
    def track_request(cls, method: str, endpoint: str, status: int):
        """Track HTTP request"""
        cls.http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
    
    @classmethod
    def track_ws_message(cls, direction: str, session_id: str):
        """Track WebSocket message"""
        cls.ws_messages_total.labels(direction=direction, session_id=session_id).inc()
    
    @classmethod
    def track_transcription(cls, duration: float):
        """Track transcription request"""
        cls.transcription_requests.inc()
        cls.transcription_duration.observe(duration)
    
    @classmethod
    def track_translation(cls, duration: float):
        """Track translation request"""
        cls.translation_requests.inc()
        cls.translation_duration.observe(duration)
    
    @classmethod
    def track_circuit_breaker(cls, name: str, is_open: bool, failure_count: int):
        """Track circuit breaker state"""
        state = 2 if is_open else 0
        cls.circuit_breaker_state.labels(name=name).set(state)
        cls.circuit_breaker_failures.labels(name=name).inc(failure_count)

# Create singleton instance
metrics = MetricsService()
