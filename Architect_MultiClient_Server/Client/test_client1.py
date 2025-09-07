import websockets
import sounddevice as sd
import soundfile as sf
import numpy as np
import json
import asyncio
import logging
import time
import random
import multiprocessing as mp
import threading
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from pathlib import Path
import queue
import signal
import psutil
import traceback
from dataclasses import dataclass
from typing import Dict, List, Optional
import gc
import os
from pathlib import Path

# Logging configuration
LOG_DIR = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Clientstress_test_logs" 
RESPONSE_LOG_DIR = os.path.join(LOG_DIR, "responses")
STATS_LOG_DIR = os.path.join(LOG_DIR, "stats")
ERROR_LOG_DIR = os.path.join(LOG_DIR, "errors")

def setup_log_directories():
    """Tạo các thư mục log cần thiết"""
    
    # Định nghĩa các thư mục log
    LOG_DIR = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Clientstress_test_logs"
    RESPONSE_LOG_DIR = os.path.join(LOG_DIR, "responses")
    STATS_LOG_DIR = os.path.join(LOG_DIR, "stats")
    ERROR_LOG_DIR = os.path.join(LOG_DIR, "errors")
    
    # Tạo các thư mục sử dụng pathlib
    directories = [LOG_DIR, RESPONSE_LOG_DIR, STATS_LOG_DIR, ERROR_LOG_DIR]
    for directory in directories:
        Path(directory).mkdir(parents=True, exist_ok=True)
    
    print(f"Created log directories:")
    print(f"  Main logs: {LOG_DIR}")
    print(f"  Response logs: {RESPONSE_LOG_DIR}")
    print(f"  Stats logs: {STATS_LOG_DIR}")  
    print(f"  Error logs: {ERROR_LOG_DIR}")
    
    return {
        'LOG_DIR': LOG_DIR,
        'RESPONSE_LOG_DIR': RESPONSE_LOG_DIR,
        'STATS_LOG_DIR': STATS_LOG_DIR,
        'ERROR_LOG_DIR': ERROR_LOG_DIR
    }

# Audio configuration
CHUNK = 320
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

# Test configuration - Optimized for long audio files
INITIAL_CLIENTS = 10
MAX_CLIENTS = 100        # Tăng lên để test thực sự
CLIENT_INCREMENT = 20    # Tăng nhanh hơn
TIME_INTERVAL = 60       # Tăng lên 1 phút cho audio dài
CLIENTS_PER_PROCESS = 20 # Tăng số clients per process
USE_PROCESSES = True      # True = multiprocessing, False = threading

# Server configuration
SERVER_URL = "ws://localhost:8000"
SESSION_ID = "stress_test_room"
TRANSCRIPT = True
TRANSLATION = True
AUDIO_FILE_PATH = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Client\testAudio.wav"

@dataclass
class ClientStats:
    client_id: str
    process_id: int
    thread_id: int
    start_time: float
    end_time: Optional[float] = None
    chunks_sent: int = 0
    bytes_sent: int = 0
    messages_received: int = 0
    errors: List[str] = None
    connection_time: float = 0
    last_activity: float = 0
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []

class StatsCollector:
    """Thread-safe stats collector"""
    def __init__(self):
        self.stats: Dict[str, ClientStats] = {}
        self.lock = threading.Lock()
        self.error_queue = queue.Queue()
        
    def update_client_stats(self, client_id: str, **kwargs):
        with self.lock:
            if client_id not in self.stats:
                self.stats[client_id] = ClientStats(
                    client_id=client_id,
                    process_id=mp.current_process().pid,
                    thread_id=threading.current_thread().ident,
                    start_time=time.time()
                )
            
            for key, value in kwargs.items():
                if hasattr(self.stats[client_id], key):
                    if key == 'errors' and isinstance(value, str):
                        self.stats[client_id].errors.append(value)
                    else:
                        setattr(self.stats[client_id], key, value)
    
    def add_error(self, client_id: str, error: str):
        self.error_queue.put((client_id, error, time.time()))
        self.update_client_stats(client_id, errors=error, last_activity=time.time())
    
    def get_summary(self) -> dict:
        with self.lock:
            total_clients = len(self.stats)
            active_clients = sum(1 for s in self.stats.values() if s.end_time is None)
            total_chunks = sum(s.chunks_sent for s in self.stats.values())
            total_bytes = sum(s.bytes_sent for s in self.stats.values())
            total_errors = sum(len(s.errors) for s in self.stats.values())
            
            return {
                'total_clients': total_clients,
                'active_clients': active_clients,
                'total_chunks_sent': total_chunks,
                'total_bytes_sent': total_bytes,
                'total_errors': total_errors,
                'processes': len(set(s.process_id for s in self.stats.values())),
                'threads': len(set(s.thread_id for s in self.stats.values()))
            }

# Global stats collector
stats_collector = StatsCollector()

def setup_process_logging(process_id: int):
    """Setup logging cho mỗi process"""
    logging.basicConfig(
        level=logging.INFO,
        format=f'%(asctime)s - PID:{process_id} - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(f'stress_test_p{process_id}.log')
        ]
    )
    return logging.getLogger(__name__)

def load_audio_data():
    """Load audio data - được share giữa các processes"""
    try:
        if not Path(AUDIO_FILE_PATH).exists():
            # Tạo audio giả
            duration = 30
            samples = int(RATE * duration)
            t = np.linspace(0, duration, samples, False)
            frequency = 440
            audio = np.sin(2 * np.pi * frequency * t) * 0.3
            return (audio * 32767).astype(np.int16)
        else:
            audio, sr = sf.read(AUDIO_FILE_PATH)
            if len(audio.shape) > 1:
                audio = np.mean(audio, axis=1)
            if sr != RATE:
                from scipy import signal
                audio = signal.resample(audio, int(len(audio) * RATE / sr))
            return (audio * 32767).astype(np.int16)
    except Exception as e:
        print(f"Error loading audio: {e}")
        # Fallback
        duration = 30
        samples = int(RATE * duration)
        t = np.linspace(0, duration, samples, False)
        audio = np.sin(2 * np.pi * 440 * t) * 0.3
        return (audio * 32767).astype(np.int16)

def get_audio_stream(file_path, chunk_size=320):
    """Stream audio chunks từ file thay vì load toàn bộ vào memory"""
    try:
        if not Path(file_path).exists():
            # Fallback: tạo audio giả
            duration = 30
            samples = int(RATE * duration)
            t = np.linspace(0, duration, samples, False)
            frequency = 440
            audio = np.sin(2 * np.pi * frequency * t) * 0.3
            audio_data = (audio * 32767).astype(np.int16)
            
            # Stream từ memory
            chunk_index = 0
            while True:
                start_idx = chunk_index * chunk_size
                end_idx = start_idx + chunk_size
                
                if start_idx >= len(audio_data):
                    chunk_index = 0
                    start_idx = 0
                    end_idx = chunk_size
                
                chunk = audio_data[start_idx:end_idx]
                if len(chunk) < chunk_size:
                    chunk = np.pad(chunk, (0, chunk_size - len(chunk)), 'constant')
                
                yield chunk
                chunk_index += 1
        else:
            # Stream từ file
            with sf.SoundFile(file_path) as f:
                while True:
                    chunk = f.read(chunk_size, dtype='int16')
                    if len(chunk) == 0:
                        # Reset to beginning for continuous loop
                        f.seek(0)
                        continue
                    if len(chunk) < chunk_size:
                        chunk = np.pad(chunk, (0, chunk_size - len(chunk)), 'constant')
                    yield chunk
    except Exception as e:
        print(f"Error in audio stream: {e}")
        # Fallback to generated audio
        duration = 30
        samples = int(RATE * duration)
        t = np.linspace(0, duration, samples, False)
        audio = np.sin(2 * np.pi * 440 * t) * 0.3
        audio_data = (audio * 32767).astype(np.int16)
        
        chunk_index = 0
        while True:
            start_idx = chunk_index * chunk_size
            end_idx = start_idx + chunk_size
            
            if start_idx >= len(audio_data):
                chunk_index = 0
                start_idx = 0
                end_idx = chunk_size
            
            chunk = audio_data[start_idx:end_idx]
            if len(chunk) < chunk_size:
                chunk = np.pad(chunk, (0, chunk_size - len(chunk)), 'constant')
            
            yield chunk
            chunk_index += 1

def get_audio_chunks(audio_data):
    """Generator cho audio chunks - DEPRECATED, sử dụng get_audio_stream thay thế"""
    chunk_index = 0
    while True:
        start_idx = chunk_index * CHUNK
        end_idx = start_idx + CHUNK
        
        if end_idx > len(audio_data):
            chunk = np.concatenate([
                audio_data[start_idx:],
                audio_data[:end_idx - len(audio_data)]
            ])
            chunk_index = (end_idx - len(audio_data)) // CHUNK
        else:
            chunk = audio_data[start_idx:end_idx]
            chunk_index += 1
            
        if len(chunk) < CHUNK:
            chunk = np.pad(chunk, (0, CHUNK - len(chunk)), 'constant')
            
        yield chunk

async def create_websocket_client(client_id: str, audio_file_path: str = None, duration: float = None):
    """Tạo một WebSocket client với streaming audio"""
    logger = logging.getLogger(__name__)
    uri = f"{SERVER_URL}/ws/vosk/?client_id={client_id}&session_id={SESSION_ID}&transcript={str(TRANSCRIPT).lower()}&translation={str(TRANSLATION).lower()}"
    
    connection_start_time = time.time()
    bytes_sent = 0
    messages_received = 0
    chunks_sent = 0
    
    try:
        # Update stats
        stats_collector.update_client_stats(
            client_id,
            start_time=connection_start_time,
            last_activity=connection_start_time
        )
        
        async with websockets.connect(
            uri,
            ping_interval=20,
            ping_timeout=10,
            close_timeout=5,
            max_size=10**6
        ) as websocket:
            
            connection_time = time.time() - connection_start_time
            stats_collector.update_client_stats(client_id, connection_time=connection_time)
            logger.info(f"Client {client_id} connected in {connection_time:.2f}s")
            
            # Receive task
            async def receive():
                nonlocal messages_received
                try:
                    async for message in websocket:
                        messages_received += 1
                        stats_collector.update_client_stats(
                            client_id,
                            messages_received=messages_received,
                            last_activity=time.time()
                        )
                        
                        if messages_received % 100 == 1:
                            try:
                                if isinstance(message, str) and message.startswith('{'):
                                    msg_data = json.loads(message)
                                    logger.debug(f"Client {client_id} received: {msg_data.get('type', 'unknown')}")
                            except json.JSONDecodeError:
                                logger.debug(f"Client {client_id} received text message")
                                
                except websockets.exceptions.ConnectionClosed:
                    logger.info(f"Client {client_id} receive connection closed")
                except Exception as e:
                    error_msg = f"Receive error: {str(e)}"
                    stats_collector.add_error(client_id, error_msg)
                    logger.error(f"Client {client_id} {error_msg}")

            recv_task = asyncio.create_task(receive())
            
            # Send audio chunks using streaming
            audio_file = audio_file_path or AUDIO_FILE_PATH
            audio_gen = get_audio_stream(audio_file, CHUNK)
            start_time = time.time()
            expected_time = start_time
            end_time = start_time + duration if duration else None
            
            try:
                while True:
                    if end_time and time.time() >= end_time:
                        logger.info(f"Client {client_id} reached duration limit")
                        break
                        
                    try:
                        chunk = next(audio_gen)
                    except StopIteration:
                        # Restart audio stream
                        audio_gen = get_audio_stream(audio_file, CHUNK)
                        chunk = next(audio_gen)
                    
                    chunk_bytes = chunk.tobytes()
                    await websocket.send(chunk_bytes)
                    
                    chunks_sent += 1
                    bytes_sent += len(chunk_bytes)
                    
                    # Update stats periodically
                    if chunks_sent % 1000 == 0:
                        stats_collector.update_client_stats(
                            client_id,
                            chunks_sent=chunks_sent,
                            bytes_sent=bytes_sent,
                            last_activity=time.time()
                        )
                    
                    # Control messages
                    if chunks_sent % 3000 == 0 and random.random() < 0.03:
                        control_msg = json.dumps({
                            "action": "set_transcript",
                            "value": random.choice([True, False])
                        })
                        await websocket.send(control_msg)
                    
                    # Timing control
                    expected_time += CHUNK / RATE
                    current_time = time.time()
                    sleep_time = expected_time - current_time
                    
                    if sleep_time > 0:
                        await asyncio.sleep(sleep_time)
                    elif sleep_time < -0.1:
                        expected_time = current_time
                        
                    # Log progress
                    if chunks_sent % 10000 == 0:
                        elapsed = time.time() - start_time
                        throughput = bytes_sent / elapsed / 1024
                        logger.info(f"Client {client_id}: {chunks_sent} chunks, {throughput:.1f} KB/s")
                        
            except asyncio.CancelledError:
                logger.info(f"Client {client_id} cancelled after {chunks_sent} chunks")
            except websockets.exceptions.ConnectionClosed:
                logger.info(f"Client {client_id} disconnected after {chunks_sent} chunks")
            except Exception as e:
                error_msg = f"Send error after {chunks_sent} chunks: {str(e)}"
                stats_collector.add_error(client_id, error_msg)
                logger.error(f"Client {client_id} {error_msg}")
            finally:
                recv_task.cancel()
                try:
                    await asyncio.wait_for(recv_task, timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
                
                # Final stats update
                total_time = time.time() - connection_start_time
                stats_collector.update_client_stats(
                    client_id,
                    end_time=time.time(),
                    chunks_sent=chunks_sent,
                    bytes_sent=bytes_sent,
                    messages_received=messages_received
                )
                
                avg_throughput = bytes_sent / total_time / 1024 if total_time > 0 else 0
                logger.info(f"Client {client_id} finished: {chunks_sent} chunks, "
                          f"{bytes_sent/1024:.1f} KB sent, {avg_throughput:.1f} KB/s avg")
                          
    except Exception as e:
        error_msg = f"Connection error: {str(e)}"
        stats_collector.add_error(client_id, error_msg)
        logger.error(f"Client {client_id} {error_msg}")
        logger.debug(f"Client {client_id} traceback: {traceback.format_exc()}")

def run_clients_in_thread(client_ids: List[str], audio_file_path: str = None, duration: float = None):
    """Chạy multiple clients trong một thread pool với streaming audio"""
    logger = logging.getLogger(__name__)
    
    async def run_multiple_clients():
        tasks = []
        for client_id in client_ids:
            task = asyncio.create_task(
                create_websocket_client(client_id, audio_file_path, duration)
            )
            tasks.append(task)
            # Delay nhỏ giữa các connections để tránh overwhelm server
            await asyncio.sleep(0.1)
        
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except Exception as e:
            logger.error(f"Error in thread {threading.current_thread().ident}: {e}")
    
    try:
        # Tạo event loop mới cho thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_multiple_clients())
    except Exception as e:
        logger.error(f"Thread error: {e}")
        logger.debug(f"Thread traceback: {traceback.format_exc()}")
    finally:
        try:
            loop.close()
        except:
            pass

def run_clients_in_process(process_id: int, client_count: int, duration: float = None):
    """Chạy clients trong một process riêng biệt với streaming audio"""
    logger = setup_process_logging(process_id)
    
    try:
        # Sử dụng streaming thay vì load toàn bộ audio
        audio_file_path = AUDIO_FILE_PATH
        logger.info(f"Process {process_id} using audio file: {audio_file_path}")
        
        # Tạo client IDs cho process này
        client_ids = [f"p{process_id}_c{i}" for i in range(client_count)]
        
        if USE_PROCESSES:
            # Sử dụng threading trong process để tăng concurrency
            max_threads = min(client_count, 20)  # Giới hạn số threads
            clients_per_thread = max(1, client_count // max_threads)
            
            with ThreadPoolExecutor(max_workers=max_threads) as executor:
                futures = []
                
                for i in range(0, client_count, clients_per_thread):
                    thread_clients = client_ids[i:i + clients_per_thread]
                    future = executor.submit(
                        run_clients_in_thread,
                        thread_clients,
                        audio_file_path,
                        duration
                    )
                    futures.append(future)
                
                # Đợi tất cả threads hoàn thành
                for future in futures:
                    try:
                        future.result(timeout=duration + 30 if duration else None)
                    except Exception as e:
                        logger.error(f"Thread future error: {e}")
        else:
            # Chỉ sử dụng asyncio
            run_clients_in_thread(client_ids, audio_file_path, duration)
            
    except Exception as e:
        logger.error(f"Process {process_id} error: {e}")
        logger.debug(f"Process {process_id} traceback: {traceback.format_exc()}")

class ResourceMonitor:
    """Monitor system resources"""
    def __init__(self):
        self.monitoring = False
        self.monitor_thread = None
        
    def start_monitoring(self):
        self.monitoring = True
        self.monitor_thread = threading.Thread(target=self._monitor_loop)
        self.monitor_thread.daemon = True
        self.monitor_thread.start()
        
    def stop_monitoring(self):
        self.monitoring = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=1)
    
    def _monitor_loop(self):
        logger = logging.getLogger(__name__)
        while self.monitoring:
            try:
                # System stats
                cpu_percent = psutil.cpu_percent(interval=1)
                memory = psutil.virtual_memory()
                
                # Network stats
                network = psutil.net_io_counters()
                
                # Process stats
                current_process = psutil.Process()
                process_memory = current_process.memory_info().rss / 1024 / 1024  # MB
                
                # Stats summary
                summary = stats_collector.get_summary()
                
                # Memory warning
                memory_warning = ""
                if memory.percent > 85:
                    memory_warning = " ⚠️ HIGH MEMORY USAGE!"
                elif memory.percent > 70:
                    memory_warning = " ⚡ Memory usage high"
                
                logger.info(f"SYSTEM - CPU: {cpu_percent}%, Memory: {memory.percent}%{memory_warning}, "
                          f"Process Memory: {process_memory:.1f}MB")
                logger.info(f"STATS - Clients: {summary['active_clients']}/{summary['total_clients']}, "
                          f"Chunks: {summary['total_chunks_sent']}, "
                          f"Errors: {summary['total_errors']}, "
                          f"Processes: {summary['processes']}")
                
                # Network throughput
                if hasattr(self, '_last_network'):
                    bytes_sent_diff = network.bytes_sent - self._last_network.bytes_sent
                    bytes_recv_diff = network.bytes_recv - self._last_network.bytes_recv
                    logger.info(f"NETWORK - Sent: {bytes_sent_diff/1024:.1f}KB, Recv: {bytes_recv_diff/1024:.1f}KB")
                
                self._last_network = network
                          
            except Exception as e:
                logger.error(f"Monitor error: {e}")
            
            time.sleep(10)  # Monitor mỗi 10 giây

class AdvancedStressTest:
    def __init__(self):
        self.processes = []
        self.resource_monitor = ResourceMonitor()
        self.setup_logging()
        
    def setup_logging(self):
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - MAIN - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('stress_test_main.log')
            ]
        )
        self.logger = logging.getLogger(__name__)
        
    def cleanup_processes(self):
        """Cleanup all processes"""
        self.logger.info("Cleaning up processes...")
        for process in self.processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                if process.is_alive():
                    process.kill()
        self.processes.clear()
        
    def run_stress_test(self, total_duration: float = 900):  # Default 15 phút
        """Chạy advanced stress test với duration control"""
        try:
            self.resource_monitor.start_monitoring()
            current_clients = INITIAL_CLIENTS
            
            self.logger.info(f"Starting stress test with duration: {total_duration}s ({total_duration/60:.1f} minutes)")
            
            while current_clients <= MAX_CLIENTS:
                self.logger.info(f"Scaling to {current_clients} clients...")
                
                # Tính toán số processes cần thiết
                if USE_PROCESSES:
                    processes_needed = max(1, (current_clients + CLIENTS_PER_PROCESS - 1) // CLIENTS_PER_PROCESS)
                    
                    # Tạo processes mới nếu cần
                    while len(self.processes) < processes_needed:
                        process_id = len(self.processes) + 1
                        clients_for_this_process = min(CLIENTS_PER_PROCESS, 
                                                     current_clients - len(self.processes) * CLIENTS_PER_PROCESS)
                        
                        if clients_for_this_process > 0:
                            process = mp.Process(
                                target=run_clients_in_process,
                                args=(process_id, clients_for_this_process, total_duration)
                            )
                            process.start()
                            self.processes.append(process)
                            self.logger.info(f"Started process {process_id} with {clients_for_this_process} clients")
                else:
                    # Sử dụng threading only
                    if not self.processes:  # Chỉ tạo một "process" (thực tế là main thread)
                        thread = threading.Thread(
                            target=run_clients_in_process,
                            args=(1, current_clients, total_duration)
                        )
                        thread.start()
                        # Wrap thread để có interface giống process
                        class ThreadWrapper:
                            def __init__(self, thread):
                                self.thread = thread
                            def is_alive(self):
                                return self.thread.is_alive()
                            def terminate(self):
                                pass
                            def join(self, timeout=None):
                                self.thread.join(timeout)
                            def kill(self):
                                pass
                        self.processes.append(ThreadWrapper(thread))
                
                # Đợi trước khi scale tiếp
                self.logger.info(f"Running with {current_clients} clients for {TIME_INTERVAL} seconds...")
                time.sleep(TIME_INTERVAL)
                
                # Kiểm tra processes
                alive_processes = sum(1 for p in self.processes if p.is_alive())
                self.logger.info(f"Alive processes: {alive_processes}/{len(self.processes)}")
                
                current_clients += CLIENT_INCREMENT
                
        except KeyboardInterrupt:
            self.logger.info("Stress test interrupted by user")
        except Exception as e:
            self.logger.error(f"Stress test error: {e}")
            self.logger.debug(f"Traceback: {traceback.format_exc()}")
        finally:
            self.cleanup_processes()
            self.resource_monitor.stop_monitoring()
            
            # Final stats
            final_stats = stats_collector.get_summary()
            self.logger.info(f"FINAL STATS: {final_stats}")

def signal_handler(signum, frame):
    """Handle shutdown signals"""
    print(f"\nReceived signal {signum}, shutting down gracefully...")
    # Set global flag để tất cả processes biết cần shutdown
    import sys
    sys.exit(0)

if __name__ == "__main__":
    # Setup log directories first
    setup_log_directories()
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "single":
            # Single client test
            print("Running single client test...")
            duration = float(sys.argv[2]) if len(sys.argv) > 2 else 60
            asyncio.run(create_websocket_client("test_client", AUDIO_FILE_PATH, duration))
        elif sys.argv[1] == "process":
            # Single process test
            print("Running single process test...")
            process_id = int(sys.argv[2]) if len(sys.argv) > 2 else 1
            client_count = int(sys.argv[3]) if len(sys.argv) > 3 else 5
            duration = float(sys.argv[4]) if len(sys.argv) > 4 else 60
            
            run_clients_in_process(process_id, client_count, duration)
        elif sys.argv[1] == "stress":
            # Full stress test with custom duration
            duration = float(sys.argv[2]) if len(sys.argv) > 2 else 900  # Default 15 phút
            print(f"Starting stress test with {duration}s duration...")
            print(f"Logs will be saved to: {LOG_DIR}")
            
            if mp.get_start_method(allow_none=True) != 'spawn':
                mp.set_start_method('spawn', force=True)
            
            stress_test = AdvancedStressTest()
            stress_test.run_stress_test(duration)
        else:
            print("Usage: python script.py [single|process|stress] [duration] [process_id] [client_count]")
            print("  single: Test single client")
            print("  process: Test single process with multiple clients")
            print("  stress: Full stress test")
    else:
        # Full stress test with default duration
        print("Starting full stress test with 15-minute duration...")
        print(f"Logs will be saved to: {LOG_DIR}")
        print(f"Response logs: {RESPONSE_LOG_DIR}")
        print(f"Stats logs: {STATS_LOG_DIR}")
        print(f"Error logs: {ERROR_LOG_DIR}")
        
        if mp.get_start_method(allow_none=True) != 'spawn':
            mp.set_start_method('spawn', force=True)
        
        stress_test = AdvancedStressTest()
        stress_test.run_stress_test(900)  # 15 phút