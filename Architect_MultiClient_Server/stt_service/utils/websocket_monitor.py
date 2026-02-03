"""
WebSocket Disconnect Monitoring Utility

This utility provides additional logging and monitoring for WebSocket disconnections
to help diagnose connection issues.
"""

import logging
from typing import Dict, List, Optional
import time
from dataclasses import dataclass
from collections import defaultdict

# Import metrics service for Prometheus integration

from ..service.metrics_service import metrics
from ..config import get_config



def _is_ws_metrics_enabled() -> bool:
    """Check if WebSocket metrics are enabled."""
    config = get_config()
    return config.metrics.enabled and config.metrics.ws_metrics

@dataclass
class DisconnectEvent:
    """Record of a WebSocket disconnect event"""
    timestamp: float
    client_id: str
    session_id: str
    code: int
    reason: str
    connection_duration: Optional[float] = None

class WebSocketMonitor:
    """Monitor WebSocket connections and disconnections"""
    
    def __init__(self):
        self.logger = logging.getLogger(f"{__name__}.WebSocketMonitor")
        self.connections: Dict[str, float] = {}  # client_id -> connection_start_time
        self.disconnect_history: List[DisconnectEvent] = []
        self.disconnect_stats = defaultdict(int)  # code -> count
        
    def record_connection(self, client_id: str, session_id: str):
        """Record when a client connects"""
        self.connections[client_id] = time.time()
        self.logger.debug(f"Recorded connection for {client_id} in session {session_id}")
        
        # Update Prometheus metrics (if enabled)
        if _is_ws_metrics_enabled():
            try:
                metrics.ws_connections.inc()
            except Exception as e:
                self.logger.debug(f"Failed to update connection metrics: {e}")
        
    def record_disconnect(self, client_id: str, session_id: str, code: int, reason: str = ""):
        """Record when a client disconnects"""
        current_time = time.time()
        connection_start = self.connections.get(client_id)
        connection_duration = None
        
        if connection_start:
            connection_duration = current_time - connection_start
            # Clean up connection record
            del self.connections[client_id]
            
        # Create disconnect event
        event = DisconnectEvent(
            timestamp=current_time,
            client_id=client_id,
            session_id=session_id,
            code=code,
            reason=reason,
            connection_duration=connection_duration
        )
        
        # Store in history (keep last 1000 events)
        self.disconnect_history.append(event)
        if len(self.disconnect_history) > 1000:
            self.disconnect_history.pop(0)
            
        # Update stats
        self.disconnect_stats[code] += 1
        
        # Update Prometheus metrics (if enabled)
        if _is_ws_metrics_enabled():
            try:
                metrics.ws_connections.dec()
                # Track disconnect by code
                metrics.ws_disconnects.labels(code=str(code)).inc()
                # Track connection duration if available
                if connection_duration:
                    metrics.ws_connection_duration.observe(connection_duration)
            except Exception as e:
                self.logger.debug(f"Failed to update disconnect metrics: {e}")
        
        # Log disconnect with appropriate level
        duration_str = f"duration={connection_duration:.2f}s" if connection_duration else "duration=unknown"
        
        if code == 1000:  # Normal closure
            self.logger.info(f"Normal disconnect: {client_id} ({duration_str}, reason='{reason}')")
        elif code == 1001:  # Going away
            self.logger.info(f"Client going away: {client_id} ({duration_str}, reason='{reason}')")
        elif code in [1006, 1011]:  # Abnormal closure
            self.logger.warning(f"Abnormal disconnect: {client_id} (code={code}, {duration_str}, reason='{reason}')")
        else:
            self.logger.warning(f"Disconnect: {client_id} (code={code}, {duration_str}, reason='{reason}')")
            
    def get_stats(self) -> dict:
        """Get disconnect statistics"""
        total_disconnects = len(self.disconnect_history)
        active_connections = len(self.connections)
        
        # Calculate average connection duration for recent disconnects
        recent_disconnects = [e for e in self.disconnect_history[-100:] if e.connection_duration]
        avg_duration = sum(e.connection_duration for e in recent_disconnects) / len(recent_disconnects) if recent_disconnects else 0
        
        return {
            "active_connections": active_connections,
            "total_disconnects": total_disconnects,
            "disconnect_codes": dict(self.disconnect_stats),
            "average_connection_duration_seconds": round(avg_duration, 2),
            "recent_disconnects": len(recent_disconnects)
        }
        
    def get_frequent_disconnect_codes(self, limit: int = 5) -> List[tuple]:
        """Get most frequent disconnect codes"""
        return sorted(self.disconnect_stats.items(), key=lambda x: x[1], reverse=True)[:limit]

# Global monitor instance
websocket_monitor = WebSocketMonitor()