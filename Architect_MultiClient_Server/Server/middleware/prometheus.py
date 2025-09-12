import time
from typing import Callable
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from .metrics_service import metrics

class PrometheusMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        method = request.method
        path = request.url.path
        
        start_time = time.time()
        
        response = await call_next(request)
        
        duration = time.time() - start_time
        status_code = response.status_code
        
        # Track request metrics
        metrics.track_request(method, path, status_code)
        metrics.http_request_duration.labels(endpoint=path).observe(duration)
        
        return response
