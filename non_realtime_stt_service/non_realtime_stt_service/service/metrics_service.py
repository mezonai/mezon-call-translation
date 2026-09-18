"""
Prometheus Metrics Service for non-realtime STT.
"""
from prometheus_client import Counter, Gauge, Histogram


class MetricsService:
    """Service for managing Prometheus metrics for non-realtime STT."""
    
    # Speech Processing metrics
    speech_segments = Counter('speech_segments_total', 'Total speech segments detected')
    audio_processing_duration = Histogram(
        'audio_processing_seconds', 
        'Time spent processing audio',
        buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0)
    )
    
    # STT metrics
    transcription_requests = Counter('transcription_requests_total', 'Total transcription requests')
    transcription_errors = Counter('transcription_errors_total', 'Transcription errors', ['error_type'])
    transcription_duration = Histogram(
        'transcription_duration_seconds',
        'Time spent on transcription',
        buckets=(0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0)
    )
    
    # Performance metrics
    cpu_usage = Gauge('cpu_usage_percent', 'CPU usage percentage')
    memory_usage = Gauge('memory_usage_bytes', 'Memory usage in bytes')
    queue_size = Gauge('queue_size', 'Current queue size', ['queue_name'])
    
    # Request metrics
    http_requests_total = Counter('http_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
    http_request_duration = Histogram(
        'http_request_duration_seconds',
        'HTTP request duration in seconds',
        ['endpoint'],
        buckets=(0.01, 0.05, 0.1, 0.5, 1.0, 2.5, 5.0)
    )

    @classmethod
    def track_request(cls, method: str, endpoint: str, status: int):
        """Track HTTP request"""
        cls.http_requests_total.labels(method=method, endpoint=endpoint, status=status).inc()
    
    @classmethod
    def track_transcription(cls, duration: float):
        """Track transcription request"""
        cls.transcription_requests.inc()
        cls.transcription_duration.observe(duration)


# Create singleton instance
metrics = MetricsService()
