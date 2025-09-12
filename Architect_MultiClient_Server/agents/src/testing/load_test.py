import asyncio
import time
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
import json
from pathlib import Path
import logging
import statistics
from src.core.websocket.manager import WebSocketManager
from src.services.metrics_service import MetricsService

@dataclass
class LoadTestConfig:
    """Load test configuration"""
    num_clients: int = 10
    messages_per_client: int = 100
    message_size_bytes: int = 1024
    send_interval: float = 0.1
    ramp_up_time: float = 5.0
    test_duration: float = 60.0
    websocket_url: str = "ws://localhost:8000/ws"

@dataclass
class ClientMetrics:
    """Metrics for a single test client"""
    client_id: str
    messages_sent: int = 0
    messages_received: int = 0
    send_errors: int = 0
    connection_errors: int = 0
    min_latency: float = float('inf')
    max_latency: float = 0
    total_latency: float = 0
    latencies: List[float] = None
    
    def __post_init__(self):
        self.latencies = []
    
    def add_latency(self, latency: float):
        """Add a latency measurement"""
        self.latencies.append(latency)
        self.min_latency = min(self.min_latency, latency)
        self.max_latency = max(self.max_latency, latency)
        self.total_latency += latency
    
    @property
    def avg_latency(self) -> float:
        """Calculate average latency"""
        return self.total_latency / len(self.latencies) if self.latencies else 0
    
    @property
    def percentile_95(self) -> float:
        """Calculate 95th percentile latency"""
        return statistics.quantiles(self.latencies, n=20)[-1] if self.latencies else 0

class LoadTest:
    """WebSocket load testing framework"""
    
    def __init__(self, config: LoadTestConfig):
        self.config = config
        self.websocket_manager = WebSocketManager(
            max_connections=config.num_clients + 5
        )
        self.metrics = MetricsService.get_instance()
        self.client_metrics: Dict[str, ClientMetrics] = {}
        self.start_time: float = 0
        self.is_running: bool = False
        
        # Setup logging
        log_file = Path("logs/load_test.log")
        log_file.parent.mkdir(parents=True, exist_ok=True)
        
        self.logger = logging.getLogger("load_test")
        handler = logging.FileHandler(log_file)
        handler.setFormatter(
            logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        )
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
    
    async def run(self):
        """Run the load test"""
        self.start_time = time.time()
        self.is_running = True
        self.logger.info(f"Starting load test with config: {self.config}")
        
        try:
            # Create and start clients
            client_tasks = []
            for i in range(self.config.num_clients):
                client_id = f"loadtest_client_{i}"
                self.client_metrics[client_id] = ClientMetrics(client_id=client_id)
                
                # Ramp up delay
                if self.config.ramp_up_time > 0:
                    delay = (i / self.config.num_clients) * self.config.ramp_up_time
                    await asyncio.sleep(delay)
                
                task = asyncio.create_task(
                    self._run_client(client_id)
                )
                client_tasks.append(task)
            
            # Wait for test duration
            await asyncio.sleep(self.config.test_duration)
            
            # Stop test
            self.is_running = False
            await asyncio.gather(*client_tasks, return_exceptions=True)
            
        finally:
            await self._cleanup()
            self._log_results()
    
    async def _run_client(self, client_id: str):
        """Run a single test client"""
        metrics = self.client_metrics[client_id]
        
        try:
            # Connect
            connection = await self.websocket_manager.create_connection(
                url=self.config.websocket_url,
                client_id=client_id,
                on_message=lambda msg: self._handle_message(client_id, msg)
            )
            
            # Send messages
            message = "X" * self.config.message_size_bytes
            messages_sent = 0
            
            while (self.is_running and 
                   messages_sent < self.config.messages_per_client):
                start_time = time.time()
                
                try:
                    await connection.send(message)
                    metrics.messages_sent += 1
                    
                    # Record latency when we receive response
                    end_time = time.time()
                    metrics.add_latency(end_time - start_time)
                    
                except Exception as e:
                    metrics.send_errors += 1
                    self.logger.error(f"Send error for {client_id}: {e}")
                
                messages_sent += 1
                await asyncio.sleep(self.config.send_interval)
                
        except Exception as e:
            metrics.connection_errors += 1
            self.logger.error(f"Client error for {client_id}: {e}")
    
    def _handle_message(self, client_id: str, message: str):
        """Handle received message"""
        if client_id in self.client_metrics:
            self.client_metrics[client_id].messages_received += 1
    
    async def _cleanup(self):
        """Cleanup resources"""
        await self.websocket_manager.close_all()
    
    def _log_results(self):
        """Log test results"""
        total_duration = time.time() - self.start_time
        
        total_sent = sum(m.messages_sent for m in self.client_metrics.values())
        total_received = sum(m.messages_received for m in self.client_metrics.values())
        total_errors = sum(m.send_errors + m.connection_errors 
                         for m in self.client_metrics.values())
        
        all_latencies = []
        for metrics in self.client_metrics.values():
            all_latencies.extend(metrics.latencies)
        
        results = {
            "test_duration": total_duration,
            "total_clients": len(self.client_metrics),
            "total_messages_sent": total_sent,
            "total_messages_received": total_received,
            "total_errors": total_errors,
            "messages_per_second": total_sent / total_duration,
            "avg_latency": statistics.mean(all_latencies) if all_latencies else 0,
            "min_latency": min(all_latencies) if all_latencies else 0,
            "max_latency": max(all_latencies) if all_latencies else 0,
            "latency_95th": statistics.quantiles(all_latencies, n=20)[-1] if all_latencies else 0,
            "error_rate": (total_errors / total_sent) if total_sent > 0 else 0
        }
        
        self.logger.info(f"Load test results: {json.dumps(results, indent=2)}")
        
        # Log detailed client metrics
        for client_id, metrics in self.client_metrics.items():
            self.logger.debug(
                f"Client {client_id} metrics: "
                f"sent={metrics.messages_sent}, "
                f"received={metrics.messages_received}, "
                f"errors={metrics.send_errors + metrics.connection_errors}, "
                f"avg_latency={metrics.avg_latency:.3f}ms"
            )
