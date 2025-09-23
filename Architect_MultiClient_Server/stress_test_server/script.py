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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import queue
import signal
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Union
import gc
import os
from collections import deque, defaultdict
from datetime import datetime
import sys
import statistics
import csv
import uuid
import contextlib
import aiohttp
import sqlite3
from enum import Enum
import pickle


# Setup multiprocessing start method early to avoid issues
if __name__ == "__main__":
    try:
        if mp.get_start_method(allow_none=True) != 'spawn':
            mp.set_start_method('spawn', force=True)
    except RuntimeError:
        # Already set, ignore
        pass

# Logging and data storage configuration
BASE_DIR = Path(__file__).resolve().parent
LOG_DIR = BASE_DIR / "stress_test_logs"
DATA_DIR = os.path.join(LOG_DIR, "raw_data")
ANALYSIS_DIR = os.path.join(LOG_DIR, "analysis")
CHARTS_DIR = os.path.join(LOG_DIR, "charts")

def setup_directories():
    """Setup output directories with proper error handling"""
    directories = [LOG_DIR, DATA_DIR, ANALYSIS_DIR, CHARTS_DIR]
    for directory in directories:
        try:
            Path(directory).mkdir(parents=True, exist_ok=True)
        except PermissionError:
            print(f"Warning: Cannot create directory {directory} - permission denied")
        except Exception as e:
            print(f"Warning: Cannot create directory {directory} - {e}")
    return directories

def setup_logging(level=logging.INFO):
    """Setup improved logging with file and console handlers"""
    try:
        # Create logs directory
        Path(LOG_DIR).mkdir(exist_ok=True)
        
        # Setup logging
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(sys.stdout),
                logging.FileHandler(os.path.join(LOG_DIR, 'stress_test.log'), encoding='utf-8')
            ]
        )
    except Exception as e:
        # Fallback to console only
        logging.basicConfig(
            level=level,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        print(f"Warning: Could not setup file logging: {e}")
    
    return logging.getLogger(__name__)

# Initialize logging
logger = setup_logging()

# Audio configuration
CHUNK = 800  # 50ms chunks at 16kHz
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

# Server configuration
SERVER_URL = "ws://localhost:8000"
SESSION_ID = "stress_test_room"
TRANSCRIPT = True
TRANSLATION = False

# Connection settings
CONNECTION_TIMEOUT = 30
RECONNECT_DELAY_BASE = 2
MAX_RECONNECT_ATTEMPTS = 3

# Test configuration
class TestMode(Enum):
    SCALING = "scaling"  # Gradual scaling test (original behavior)
    FIXED = "fixed"      # Fixed number of clients for fixed duration

# Get audio file path with fallback
def get_audio_file_path():
    """Get audio file path with multiple fallback options"""
    candidates = [
        os.path.join(os.path.dirname(__file__), "1756366843624_en_output.wav"),
        os.path.join(os.path.dirname(__file__), "test_audio.wav"),
        "test_audio.wav",
        "audio.wav"
    ]
    
    for path in candidates:
        if os.path.exists(path):
            return path
    
    # No file found, will use generated audio
    return None

AUDIO_FILE_PATH = get_audio_file_path()

@dataclass
class TestResult:
    """Raw test data point"""
    timestamp: float
    client_id: str
    event_type: str  # 'chunk_sent', 'transcript_received', 'error', 'connection'
    data: Dict
    test_session_id: str

@dataclass
class LatencyMeasurement:
    """Single latency measurement"""
    client_id: str
    timestamp: float
    send_time: float
    receive_time: float
    latency_ms: float
    transcript_text: Optional[str] = None
    chunk_id: Optional[str] = None

class DataLogger:
    """Thread-safe data logger that stores all test data"""
    
    def __init__(self, test_session_id: str):
        self.test_session_id = test_session_id
        self.db_path = os.path.join(DATA_DIR, f"test_data_{test_session_id}.db")
        self.csv_path = os.path.join(DATA_DIR, f"test_data_{test_session_id}.csv")
        self.lock = threading.Lock()
        self._setup_database()
        self._setup_csv()
        
    def _setup_database(self):
        """Setup SQLite database for structured data storage"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS test_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp REAL NOT NULL,
                        client_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        data TEXT NOT NULL,
                        test_session_id TEXT NOT NULL
                    )
                ''')
                
                conn.execute('''
                    CREATE TABLE IF NOT EXISTS latency_measurements (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        client_id TEXT NOT NULL,
                        timestamp REAL NOT NULL,
                        send_time REAL NOT NULL,
                        receive_time REAL NOT NULL,
                        latency_ms REAL NOT NULL,
                        transcript_text TEXT,
                        chunk_id TEXT,
                        test_session_id TEXT NOT NULL
                    )
                ''')
                
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_test_events_timestamp 
                    ON test_events(timestamp)
                ''')
                
                conn.execute('''
                    CREATE INDEX IF NOT EXISTS idx_latency_timestamp 
                    ON latency_measurements(timestamp)
                ''')
                
        except Exception as e:
            logger.error(f"Database setup error: {e}")
    
    def _setup_csv(self):
        """Setup CSV file for easy analysis"""
        try:
            with open(self.csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    'timestamp', 'client_id', 'event_type', 'latency_ms', 
                    'bytes_sent', 'transcript_text', 'error_message'
                ])
        except Exception as e:
            logger.error(f"CSV setup error: {e}")
    
    def log_event(self, result: TestResult):
        """Log a test event"""
        with self.lock:
            try:
                # SQLite storage
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('''
                        INSERT INTO test_events 
                        (timestamp, client_id, event_type, data, test_session_id)
                        VALUES (?, ?, ?, ?, ?)
                    ''', (result.timestamp, result.client_id, result.event_type, 
                          json.dumps(result.data), result.test_session_id))
                
                # CSV storage for easy analysis
                with open(self.csv_path, 'a', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        result.timestamp, 
                        result.client_id, 
                        result.event_type,
                        result.data.get('latency_ms', ''),
                        result.data.get('bytes_sent', ''),
                        result.data.get('transcript_text', '')[:100] if result.data.get('transcript_text') else '',
                        result.data.get('error_message', '')
                    ])
                    
            except Exception as e:
                logger.error(f"Data logging error: {e}")
    
    def log_latency(self, measurement: LatencyMeasurement):
        """Log a latency measurement"""
        with self.lock:
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('''
                        INSERT INTO latency_measurements 
                        (client_id, timestamp, send_time, receive_time, latency_ms, 
                         transcript_text, chunk_id, test_session_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (measurement.client_id, measurement.timestamp, 
                          measurement.send_time, measurement.receive_time, 
                          measurement.latency_ms, measurement.transcript_text,
                          measurement.chunk_id, self.test_session_id))
                    
            except Exception as e:
                logger.error(f"Latency logging error: {e}")

class SimpleStatsCollector:
    """Lightweight stats collector for live monitoring only"""
    
    def __init__(self):
        self.active_clients = set()
        self.total_chunks = 0
        self.total_bytes = 0
        self.total_errors = 0
        self.lock = threading.Lock()
        
    def update_stats(self, client_id: str, chunks_sent: int = 0, bytes_sent: int = 0, errors: int = 0):
        """Update basic statistics"""
        with self.lock:
            self.active_clients.add(client_id)
            self.total_chunks += chunks_sent
            self.total_bytes += bytes_sent
            self.total_errors += errors
    
    def get_summary(self) -> Dict:
        """Get basic summary for live monitoring"""
        with self.lock:
            return {
                'active_clients': len(self.active_clients),
                'total_chunks': self.total_chunks,
                'total_bytes_mb': self.total_bytes / (1024 * 1024),
                'total_errors': self.total_errors
            }

def get_audio_stream(file_path: Optional[str], chunk_size: int = 800):
    """Improved audio stream generator with better error handling"""
    try:
        if file_path and Path(file_path).exists():
            # Stream from file
            logger.info(f"Streaming audio from file: {file_path}")
            try:
                with sf.SoundFile(file_path) as f:
                    if f.samplerate != RATE:
                        logger.warning(f"Audio file sample rate {f.samplerate} != {RATE}, may cause issues")
                    
                    while True:
                        chunk = f.read(chunk_size, dtype='int16', always_2d=False)
                        if len(chunk) == 0:
                            f.seek(0)  # Loop the file
                            continue
                        if len(chunk) < chunk_size:
                            chunk = np.pad(chunk, (0, chunk_size - len(chunk)), 'constant')
                        yield chunk
                        
            except Exception as e:
                logger.error(f"Error reading audio file {file_path}: {e}")
                # Fall back to generated audio
                file_path = None
        
        if not file_path:
            # Generate test audio
            logger.info("Generating synthetic test audio (sine wave)")
            duration = 30
            samples = int(RATE * duration)
            t = np.linspace(0, duration, samples, False)
            
            # Create a more complex waveform (mix of frequencies)
            frequency_base = 440  # A4
            audio = (
                0.3 * np.sin(2 * np.pi * frequency_base * t) +
                0.2 * np.sin(2 * np.pi * frequency_base * 1.5 * t) +
                0.1 * np.sin(2 * np.pi * frequency_base * 2 * t)
            )
            
            # Add some variation to make it more interesting
            audio *= (0.8 + 0.2 * np.sin(2 * np.pi * 0.5 * t))  # Amplitude modulation
            
            audio_data = (audio * 32767 * 0.7).astype(np.int16)  # Leave headroom
            
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
                
    except Exception as e:
        logger.error(f"Audio stream error: {e}")
        # Ultimate fallback: silence
        logger.warning("Falling back to silence")
        while True:
            yield np.zeros(chunk_size, dtype=np.int16)

async def data_collecting_websocket_client(
    client_id: str, 
    data_logger: DataLogger, 
    stats_collector: SimpleStatsCollector,
    audio_file_path: Optional[str] = None, 
    duration: Optional[float] = None
):
    """WebSocket client that focuses on data collection"""
    
    uri = f"{SERVER_URL}/ws/vosk/?client_id={client_id}&session_id={SESSION_ID}&transcript={str(TRANSCRIPT).lower()}&translation={str(TRANSLATION).lower()}"
    
    connection_start_time = time.time()
    chunks_sent = 0
    bytes_sent = 0
    messages_received = 0
    transcripts_received = 0
    pending_chunks = {}  # chunk_id -> send_timestamp
    retry_count = 0
    
    # Log client start
    data_logger.log_event(TestResult(
        timestamp=time.time(),
        client_id=client_id,
        event_type='connection_start',
        data={'connection_start_time': connection_start_time},
        test_session_id=data_logger.test_session_id
    ))
    
    while retry_count <= MAX_RECONNECT_ATTEMPTS:
        try:
            async with websockets.connect(
                uri,
                ping_interval=30,
                ping_timeout=20,
                close_timeout=10,
                max_size=10**6,
                compression=None
            ) as websocket:
                
                connection_time = time.time() - connection_start_time
                
                # Log successful connection
                data_logger.log_event(TestResult(
                    timestamp=time.time(),
                    client_id=client_id,
                    event_type='connection_success',
                    data={'connection_time_ms': connection_time * 1000},
                    test_session_id=data_logger.test_session_id
                ))
                
                # Receive task
                async def receive():
                    nonlocal messages_received, transcripts_received
                    
                    try:
                        async for message in websocket:
                            receive_timestamp = time.time()
                            messages_received += 1
                            
                            # Log message received
                            data_logger.log_event(TestResult(
                                timestamp=receive_timestamp,
                                client_id=client_id,
                                event_type='message_received',
                                data={'message_size': len(message) if isinstance(message, str) else len(message)},
                                test_session_id=data_logger.test_session_id
                            ))
                            
                            try:
                                if isinstance(message, str) and message.strip().startswith('{'):
                                    msg_data = json.loads(message)
                                    
                                    if 'text' in msg_data:
                                        transcript_text = msg_data.get('text', '').strip()
                                        if transcript_text:
                                            transcripts_received += 1
                                            
                                            # Log transcript
                                            data_logger.log_event(TestResult(
                                                timestamp=receive_timestamp,
                                                client_id=client_id,
                                                event_type='transcript_received',
                                                data={
                                                    'transcript_text': transcript_text,
                                                    'transcript_length': len(transcript_text)
                                                },
                                                test_session_id=data_logger.test_session_id
                                            ))
                                            
                                            # Calculate and log latency (FIFO matching)
                                            if pending_chunks:
                                                chunk_id = min(pending_chunks.keys())
                                                send_time = pending_chunks.pop(chunk_id)
                                                latency_ms = (receive_timestamp - send_time) * 1000
                                                
                                                # Log latency measurement
                                                data_logger.log_latency(LatencyMeasurement(
                                                    client_id=client_id,
                                                    timestamp=receive_timestamp,
                                                    send_time=send_time,
                                                    receive_time=receive_timestamp,
                                                    latency_ms=latency_ms,
                                                    transcript_text=transcript_text,
                                                    chunk_id=chunk_id
                                                ))
                                            
                            except json.JSONDecodeError:
                                pass  # Ignore non-JSON messages
                            except Exception as e:
                                # Log processing error
                                data_logger.log_event(TestResult(
                                    timestamp=time.time(),
                                    client_id=client_id,
                                    event_type='error',
                                    data={'error_message': f'Message processing error: {str(e)}'},
                                    test_session_id=data_logger.test_session_id
                                ))
                            
                    except websockets.exceptions.ConnectionClosed:
                        logger.info(f"Client {client_id} receive connection closed")
                    except Exception as e:
                        # Log receive error
                        data_logger.log_event(TestResult(
                            timestamp=time.time(),
                            client_id=client_id,
                            event_type='error',
                            data={'error_message': f'Receive error: {str(e)}'},
                            test_session_id=data_logger.test_session_id
                        ))

                recv_task = asyncio.create_task(receive())
                
                # Audio streaming
                try:
                    audio_gen = get_audio_stream(audio_file_path or AUDIO_FILE_PATH, CHUNK)
                    start_time = time.time()
                    end_time = start_time + duration if duration else None
                    
                    chunk_duration = CHUNK / RATE
                    next_chunk_time = start_time
                    chunk_id = 0
                    
                    while True:
                        current_time = time.time()
                        
                        if end_time and current_time >= end_time:
                            break
                        
                        if recv_task.done():
                            break
                        
                        # Wait for next chunk time
                        sleep_time = next_chunk_time - current_time
                        if sleep_time > 0:
                            await asyncio.sleep(sleep_time)
                        
                        # Get audio chunk
                        try:
                            chunk = next(audio_gen)
                        except StopIteration:
                            audio_gen = get_audio_stream(audio_file_path or AUDIO_FILE_PATH, CHUNK)
                            chunk = next(audio_gen)
                        except Exception as e:
                            # Log audio generation error
                            data_logger.log_event(TestResult(
                                timestamp=time.time(),
                                client_id=client_id,
                                event_type='error',
                                data={'error_message': f'Audio generation error: {str(e)}'},
                                test_session_id=data_logger.test_session_id
                            ))
                            break
                        
                        # Send chunk
                        chunk_bytes = chunk.tobytes()
                        send_timestamp = time.time()
                        
                        try:
                            await asyncio.wait_for(websocket.send(chunk_bytes), timeout=5.0)
                            
                            chunks_sent += 1
                            bytes_sent += len(chunk_bytes)
                            pending_chunks[chunk_id] = send_timestamp
                            
                            # Log chunk sent
                            data_logger.log_event(TestResult(
                                timestamp=send_timestamp,
                                client_id=client_id,
                                event_type='chunk_sent',
                                data={
                                    'chunk_id': chunk_id,
                                    'bytes_sent': len(chunk_bytes),
                                    'cumulative_chunks': chunks_sent,
                                    'cumulative_bytes': bytes_sent
                                },
                                test_session_id=data_logger.test_session_id
                            ))
                            
                            chunk_id += 1
                            
                            # Clean old pending chunks
                            if len(pending_chunks) > 100:
                                oldest_chunk = min(pending_chunks.keys())
                                del pending_chunks[oldest_chunk]
                            
                        except asyncio.TimeoutError:
                            # Log timeout error
                            data_logger.log_event(TestResult(
                                timestamp=time.time(),
                                client_id=client_id,
                                event_type='error',
                                data={'error_message': 'Send timeout'},
                                test_session_id=data_logger.test_session_id
                            ))
                            break
                        except Exception as e:
                            # Log send error
                            data_logger.log_event(TestResult(
                                timestamp=time.time(),
                                client_id=client_id,
                                event_type='error',
                                data={'error_message': f'Send error: {str(e)}'},
                                test_session_id=data_logger.test_session_id
                            ))
                            break
                        
                        # Schedule next chunk
                        next_chunk_time += chunk_duration
                        
                        # Update stats periodically
                        if chunks_sent % 200 == 0:
                            stats_collector.update_stats(client_id, 200, len(chunk_bytes) * 200)
                            
                except Exception as e:
                    # Log streaming error
                    data_logger.log_event(TestResult(
                        timestamp=time.time(),
                        client_id=client_id,
                        event_type='error',
                        data={'error_message': f'Streaming error: {str(e)}'},
                        test_session_id=data_logger.test_session_id
                    ))
                finally:
                    # Cleanup
                    if not recv_task.done():
                        recv_task.cancel()
                        with contextlib.suppress(asyncio.TimeoutError):
                            await asyncio.wait_for(recv_task, timeout=1.0)
                    
                    # Log completion
                    data_logger.log_event(TestResult(
                        timestamp=time.time(),
                        client_id=client_id,
                        event_type='client_completed',
                        data={
                            'total_chunks_sent': chunks_sent,
                            'total_bytes_sent': bytes_sent,
                            'messages_received': messages_received,
                            'transcripts_received': transcripts_received,
                            'total_duration_s': time.time() - connection_start_time
                        },
                        test_session_id=data_logger.test_session_id
                    ))
                          
            # Successfully completed
            break
                          
        except Exception as e:
            retry_count += 1
            error_msg = f"Connection error (attempt {retry_count}/{MAX_RECONNECT_ATTEMPTS + 1}): {str(e)}"
            
            # Log retry attempt
            data_logger.log_event(TestResult(
                timestamp=time.time(),
                client_id=client_id,
                event_type='error',
                data={
                    'error_message': error_msg,
                    'retry_attempt': retry_count
                },
                test_session_id=data_logger.test_session_id
            ))
            
            if retry_count <= MAX_RECONNECT_ATTEMPTS:
                delay = min(RECONNECT_DELAY_BASE * (2 ** (retry_count - 1)), 10)
                logger.info(f"Client {client_id} retrying in {delay:.1f}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Client {client_id} exhausted all retry attempts")
                break

def run_clients_in_process(
    process_id: int, 
    client_count: int, 
    test_session_id: str,
    duration: Optional[float] = None
):
    """Run clients in separate process"""
    async def run_multiple_clients():
        # Each process creates its own data logger
        data_logger = DataLogger(f"{test_session_id}_p{process_id}")
        stats_collector = SimpleStatsCollector()
        
        tasks = []
        for i in range(client_count):
            client_id = f"p{process_id}_c{i}"
            task = asyncio.create_task(
                data_collecting_websocket_client(
                    client_id, data_logger, stats_collector, AUDIO_FILE_PATH, duration
                )
            )
            tasks.append(task)
            if i < client_count - 1:  # Don't sleep after the last client
                await asyncio.sleep(0.2)  # Stagger client starts
        
        try:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Process {process_id} Client {i} failed: {result}")
        except Exception as e:
            logger.error(f"Process {process_id} error: {e}")
    
    try:
        # Create new event loop for this process
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_multiple_clients())
        logger.info(f"Process {process_id} completed successfully")
    except Exception as e:
        logger.error(f"Process {process_id} execution error: {e}")
    finally:
        try:
            # Cleanup pending tasks
            pending = asyncio.all_tasks(loop)
            if pending:
                for task in pending:
                    task.cancel()
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            
            loop.close()
        except Exception as e:
            logger.debug(f"Process {process_id} loop cleanup error: {e}")

class TestOrchestrator:
    """Main test orchestrator with different test modes"""
    
    def __init__(self):
        self.processes = []
        self.should_stop = False
        
    def cleanup_processes(self):
        """Cleanup all processes"""
        for process in self.processes:
            if process.is_alive():
                try:
                    process.terminate()
                    process.join(timeout=5)
                    if process.is_alive():
                        process.kill()
                        process.join(timeout=2)
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")
        self.processes.clear()
    
    def run_fixed_client_test(self, num_clients: int, duration: float):
        """Run test with fixed number of clients for fixed duration"""
        try:
            setup_directories()
            
            test_session_id = f"fixed_{num_clients}c_{int(duration)}s_{int(time.time())}"
            
            print(f"\n" + "=" * 70)
            print(f"Fixed Client Count Test")
            print(f"=" * 70)
            print(f"Clients: {num_clients}")
            print(f"Duration: {duration}s ({duration/60:.1f} minutes)")
            print(f"Session ID: {test_session_id}")
            print(f"Data will be saved to: {DATA_DIR}")
            print(f"=" * 70)
            
            # Setup signal handlers for graceful shutdown
            def signal_handler(signum, frame):
                print(f"\nReceived signal {signum}, stopping test...")
                self.should_stop = True
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            # Determine number of processes needed
            clients_per_process = 5  # Fixed number of clients per process
            processes_needed = max(1, (num_clients + clients_per_process - 1) // clients_per_process)
            
            print(f"Starting {processes_needed} processes...")
            
            # Create and start processes
            for process_id in range(processes_needed):
                clients_for_process = min(
                    clients_per_process,
                    num_clients - process_id * clients_per_process
                )
                
                if clients_for_process > 0 and not self.should_stop:
                    try:
                        process = mp.Process(
                            target=run_clients_in_process,
                            args=(process_id + 1, clients_for_process, test_session_id, duration)
                        )
                        process.start()
                        self.processes.append(process)
                        print(f"Started process {process_id + 1} with {clients_for_process} clients")
                        time.sleep(1)  # Brief delay between process starts
                    except Exception as e:
                        logger.error(f"Failed to start process {process_id + 1}: {e}")
            
            print(f"\nAll processes started. Test will run for {duration}s...")
            print(f"Data is being collected in background. Use Ctrl+C to stop early.")
            
            # Monitor test progress
            start_time = time.time()
            last_update = start_time
            
            while True:
                current_time = time.time()
                elapsed = current_time - start_time
                
                if elapsed >= duration or self.should_stop:
                    print(f"\nTest duration reached. Waiting for processes to complete...")
                    break
                
                # Progress update every 30 seconds
                if current_time - last_update >= 30:
                    remaining = duration - elapsed
                    alive_processes = sum(1 for p in self.processes if p.is_alive())
                    print(f"Progress: {elapsed:.0f}s/{duration:.0f}s elapsed, "
                          f"{remaining:.0f}s remaining, {alive_processes}/{len(self.processes)} processes active")
                    last_update = current_time
                
                time.sleep(5)
            
            # Wait for all processes to complete
            for process in self.processes:
                if process.is_alive():
                    process.join(timeout=30)  # Give processes time to cleanup
                    if process.is_alive():
                        logger.warning(f"Process {process.pid} did not terminate cleanly, forcing...")
                        process.terminate()
                        process.join(timeout=5)
            
            print(f"\nFixed client test completed!")
            print(f"Data saved to: {DATA_DIR}")
            print(f"Session ID: {test_session_id}")
            print(f"Use the 'analyze' command to process the collected data.")
            
        except KeyboardInterrupt:
            print(f"\nTest interrupted by user")
            self.should_stop = True
        except Exception as e:
            logger.error(f"Fixed client test error: {e}")
        finally:
            self.cleanup_processes()
    
    def run_scaling_stress_test(self, total_duration: float = 300):
        """Run the original scaling stress test"""
        try:
            setup_directories()
            
            test_session_id = f"scaling_{int(total_duration)}s_{int(time.time())}"
            
            # Test configuration for scaling
            initial_clients = 5
            max_clients = 50
            client_increment = 5
            time_interval = 60
            
            current_clients = initial_clients
            test_start_time = time.time()
            
            print(f"\n" + "=" * 70)
            print(f"Scaling Stress Test")
            print(f"=" * 70)
            print(f"Duration: {total_duration}s ({total_duration/60:.1f} minutes)")
            print(f"Client scaling: {initial_clients} -> {max_clients} (increment: {client_increment})")
            print(f"Interval: {time_interval}s per level")
            print(f"Session ID: {test_session_id}")
            print(f"Data will be saved to: {DATA_DIR}")
            print(f"=" * 70)
            
            # Setup signal handlers for graceful shutdown
            def signal_handler(signum, frame):
                print(f"\nReceived signal {signum}, stopping test...")
                self.should_stop = True
            
            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
            
            while current_clients <= max_clients and (time.time() - test_start_time) < total_duration and not self.should_stop:
                level_start_time = time.time()
                print(f"\nScaling to {current_clients} clients...")
                
                remaining_time = total_duration - (time.time() - test_start_time)
                level_duration = min(time_interval, remaining_time)
                
                # Create processes for this level
                clients_per_process = 5
                processes_needed = max(1, (current_clients + clients_per_process - 1) // clients_per_process)
                
                # Clear previous processes
                self.cleanup_processes()
                
                for process_id in range(processes_needed):
                    clients_for_process = min(
                        clients_per_process,
                        current_clients - process_id * clients_per_process
                    )
                    
                    if clients_for_process > 0 and not self.should_stop:
                        try:
                            process = mp.Process(
                                target=run_clients_in_process,
                                args=(process_id + 1, clients_for_process, f"{test_session_id}_level{current_clients}", level_duration)
                            )
                            process.start()
                            self.processes.append(process)
                            print(f"Started process {process_id + 1} with {clients_for_process} clients")
                            time.sleep(1)  # Brief delay between process starts
                        except Exception as e:
                            logger.error(f"Failed to start process {process_id + 1}: {e}")
                
                # Monitor this level
                level_end_time = level_start_time + level_duration
                last_report = level_start_time
                
                while time.time() < level_end_time and not self.should_stop:
                    time.sleep(10)
                    
                    current_time = time.time()
                    alive_processes = sum(1 for p in self.processes if p.is_alive())
                    
                    if alive_processes == 0:
                        print("All processes completed for this level")
                        break
                    
                    # Periodic reporting
                    if current_time - last_report >= 30:
                        remaining = level_end_time - current_time
                        print(f"Level {current_clients}: {alive_processes}/{len(self.processes)} processes active, {remaining:.0f}s remaining")
                        last_report = current_time
                
                # Wait for level completion
                for process in self.processes:
                    if process.is_alive():
                        process.join(timeout=10)
                
                current_clients += client_increment
                
            print(f"\nScaling stress test completed!")
            print(f"Data saved to: {DATA_DIR}")
            print(f"Session ID: {test_session_id}")
            print(f"Use the 'analyze' command to process the collected data.")
            
        except KeyboardInterrupt:
            print(f"\nTest interrupted by user")
            self.should_stop = True
        except Exception as e:
            logger.error(f"Scaling stress test error: {e}")
        finally:
            self.cleanup_processes()

class DataAnalyzer:
    """Analyze collected test data and generate comprehensive reports"""
    
    def __init__(self, data_dir: str = DATA_DIR):
        self.data_dir = data_dir
    
    def list_available_sessions(self) -> List[str]:
        """List all available test sessions"""
        sessions = []
        try:
            for file in Path(self.data_dir).glob("test_data_*.db"):
                session_id = file.stem.replace("test_data_", "")
                sessions.append(session_id)
        except Exception as e:
            logger.error(f"Error listing sessions: {e}")
        return sorted(sessions)
    
    def analyze_session(self, session_id: str) -> Dict:
        """Analyze a specific test session"""
        db_path = os.path.join(self.data_dir, f"test_data_{session_id}.db")
        
        if not os.path.exists(db_path):
            raise FileNotFoundError(f"No data found for session: {session_id}")
        
        analysis = {}
        
        try:
            with sqlite3.connect(db_path) as conn:
                # Basic test statistics
                cursor = conn.execute('''
                    SELECT 
                        COUNT(DISTINCT client_id) as total_clients,
                        COUNT(*) as total_events,
                        MIN(timestamp) as start_time,
                        MAX(timestamp) as end_time
                    FROM test_events
                ''')
                basic_stats = cursor.fetchone()
                
                analysis['basic_stats'] = {
                    'total_clients': basic_stats[0],
                    'total_events': basic_stats[1],
                    'test_duration_s': basic_stats[3] - basic_stats[2] if basic_stats[2] and basic_stats[3] else 0,
                    'start_time': datetime.fromtimestamp(basic_stats[2]) if basic_stats[2] else None,
                    'end_time': datetime.fromtimestamp(basic_stats[3]) if basic_stats[3] else None
                }
                
                # Chunks and throughput analysis
                cursor = conn.execute('''
                    SELECT 
                        client_id,
                        COUNT(*) as chunks_sent,
                        SUM(CAST(json_extract(data, '$.bytes_sent') AS INTEGER)) as total_bytes
                    FROM test_events 
                    WHERE event_type = 'chunk_sent'
                    GROUP BY client_id
                ''')
                
                client_throughput = []
                total_chunks = 0
                total_bytes = 0
                
                for row in cursor.fetchall():
                    client_id, chunks, bytes_sent = row
                    if chunks and bytes_sent:
                        client_throughput.append({
                            'client_id': client_id,
                            'chunks_sent': chunks,
                            'bytes_sent': bytes_sent
                        })
                        total_chunks += chunks
                        total_bytes += bytes_sent
                
                analysis['throughput'] = {
                    'total_chunks_sent': total_chunks,
                    'total_bytes_sent': total_bytes,
                    'total_mb_sent': total_bytes / (1024 * 1024),
                    'avg_throughput_mbps': (total_bytes * 8 / (1024 * 1024)) / max(analysis['basic_stats']['test_duration_s'], 1) if analysis['basic_stats']['test_duration_s'] > 0 else 0,
                    'client_throughput': client_throughput
                }
                
                # Latency analysis
                cursor = conn.execute('''
                    SELECT 
                        client_id,
                        latency_ms,
                        timestamp
                    FROM latency_measurements
                    ORDER BY timestamp
                ''')
                
                latency_data = []
                client_latencies = defaultdict(list)
                
                for row in cursor.fetchall():
                    client_id, latency_ms, timestamp = row
                    latency_data.append({
                        'client_id': client_id,
                        'latency_ms': latency_ms,
                        'timestamp': timestamp
                    })
                    client_latencies[client_id].append(latency_ms)
                
                if latency_data:
                    all_latencies = [d['latency_ms'] for d in latency_data]
                    analysis['latency'] = {
                        'total_measurements': len(all_latencies),
                        'avg_latency_ms': statistics.mean(all_latencies),
                        'median_latency_ms': statistics.median(all_latencies),
                        'min_latency_ms': min(all_latencies),
                        'max_latency_ms': max(all_latencies),
                        'std_latency_ms': statistics.stdev(all_latencies) if len(all_latencies) > 1 else 0,
                        'p95_latency_ms': np.percentile(all_latencies, 95),
                        'p99_latency_ms': np.percentile(all_latencies, 99),
                        'client_latencies': dict(client_latencies)
                    }
                else:
                    analysis['latency'] = {
                        'total_measurements': 0,
                        'message': 'No latency measurements found'
                    }
                
                # Error analysis
                cursor = conn.execute('''
                    SELECT 
                        client_id,
                        json_extract(data, '$.error_message') as error_message
                    FROM test_events 
                    WHERE event_type = 'error'
                ''')
                
                errors = []
                client_errors = defaultdict(int)
                error_types = defaultdict(int)
                
                for row in cursor.fetchall():
                    client_id, error_message = row
                    errors.append({
                        'client_id': client_id,
                        'error_message': error_message
                    })
                    client_errors[client_id] += 1
                    
                    # Categorize error types
                    if 'timeout' in error_message.lower():
                        error_types['timeout'] += 1
                    elif 'connection' in error_message.lower():
                        error_types['connection'] += 1
                    else:
                        error_types['other'] += 1
                
                analysis['errors'] = {
                    'total_errors': len(errors),
                    'error_rate_percent': (len(errors) / max(total_chunks, 1)) * 100,
                    'client_errors': dict(client_errors),
                    'error_types': dict(error_types),
                    'errors': errors
                }
                
                # Performance assessment
                analysis['assessment'] = self._generate_performance_assessment(analysis)
                
        except Exception as e:
            logger.error(f"Error analyzing session {session_id}: {e}")
            raise
        
        return analysis
    
    def _generate_performance_assessment(self, analysis: Dict) -> Dict:
        """Generate performance assessment based on analysis"""
        assessment = {
            'overall_grade': 'Unknown',
            'latency_grade': 'Unknown',
            'throughput_grade': 'Unknown',
            'reliability_grade': 'Unknown',
            'recommendations': []
        }
        
        try:
            # Latency assessment
            if 'latency' in analysis and 'avg_latency_ms' in analysis['latency']:
                avg_latency = analysis['latency']['avg_latency_ms']
                if avg_latency < 200:
                    assessment['latency_grade'] = 'Excellent'
                elif avg_latency < 500:
                    assessment['latency_grade'] = 'Good'
                elif avg_latency < 1000:
                    assessment['latency_grade'] = 'Fair'
                else:
                    assessment['latency_grade'] = 'Poor'
                    assessment['recommendations'].append('Consider optimizing server processing pipeline for lower latency')
            
            # Throughput assessment
            if analysis['basic_stats']['test_duration_s'] > 0:
                duration = analysis['basic_stats']['test_duration_s']
                expected_chunks = analysis['basic_stats']['total_clients'] * duration * (RATE / CHUNK)
                actual_chunks = analysis['throughput']['total_chunks_sent']
                throughput_ratio = actual_chunks / expected_chunks if expected_chunks > 0 else 0
                
                if throughput_ratio >= 0.95:
                    assessment['throughput_grade'] = 'Excellent'
                elif throughput_ratio >= 0.85:
                    assessment['throughput_grade'] = 'Good'
                elif throughput_ratio >= 0.70:
                    assessment['throughput_grade'] = 'Fair'
                else:
                    assessment['throughput_grade'] = 'Poor'
                    assessment['recommendations'].append('Server struggling to keep up with real-time audio processing')
            
            # Reliability assessment
            error_rate = analysis['errors']['error_rate_percent']
            if error_rate < 1:
                assessment['reliability_grade'] = 'Excellent'
            elif error_rate < 3:
                assessment['reliability_grade'] = 'Good'
            elif error_rate < 10:
                assessment['reliability_grade'] = 'Fair'
                assessment['recommendations'].append('Investigate connection stability issues')
            else:
                assessment['reliability_grade'] = 'Poor'
                assessment['recommendations'].append('Significant reliability issues detected - review server configuration')
            
            # Overall assessment
            grades = [assessment['latency_grade'], assessment['throughput_grade'], assessment['reliability_grade']]
            grade_scores = {'Excellent': 4, 'Good': 3, 'Fair': 2, 'Poor': 1, 'Unknown': 0}
            avg_score = sum(grade_scores.get(g, 0) for g in grades) / len(grades)
            
            if avg_score >= 3.5:
                assessment['overall_grade'] = 'Excellent'
            elif avg_score >= 2.5:
                assessment['overall_grade'] = 'Good'
            elif avg_score >= 1.5:
                assessment['overall_grade'] = 'Fair'
            else:
                assessment['overall_grade'] = 'Poor'
            
        except Exception as e:
            logger.error(f"Error in performance assessment: {e}")
        
        return assessment
    
    def generate_report(self, session_id: str, save_to_file: bool = True) -> str:
        """Generate comprehensive analysis report"""
        try:
            analysis = self.analyze_session(session_id)
            
            report_lines = []
            report_lines.append("=" * 80)
            report_lines.append("VOSK SERVER STRESS TEST - ANALYSIS REPORT")
            report_lines.append("=" * 80)
            report_lines.append(f"Session ID: {session_id}")
            
            # Basic statistics
            basic = analysis['basic_stats']
            report_lines.append(f"\nTEST OVERVIEW:")
            report_lines.append(f"  Start Time: {basic['start_time']}")
            report_lines.append(f"  End Time: {basic['end_time']}")
            report_lines.append(f"  Duration: {basic['test_duration_s']:.1f} seconds ({basic['test_duration_s']/60:.1f} minutes)")
            report_lines.append(f"  Total Clients: {basic['total_clients']}")
            report_lines.append(f"  Total Events: {basic['total_events']:,}")
            
            # Throughput analysis
            throughput = analysis['throughput']
            report_lines.append(f"\nTHROUGHPUT ANALYSIS:")
            report_lines.append(f"  Total Chunks Sent: {throughput['total_chunks_sent']:,}")
            report_lines.append(f"  Total Data Sent: {throughput['total_mb_sent']:.2f} MB")
            report_lines.append(f"  Average Throughput: {throughput['avg_throughput_mbps']:.2f} Mbps")
            
            # Latency analysis
            if 'avg_latency_ms' in analysis.get('latency', {}):
                latency = analysis['latency']
                report_lines.append(f"\nLATENCY ANALYSIS:")
                report_lines.append(f"  Total Measurements: {latency['total_measurements']:,}")
                report_lines.append(f"  Average Latency: {latency['avg_latency_ms']:.1f} ms")
                report_lines.append(f"  Median Latency: {latency['median_latency_ms']:.1f} ms")
                report_lines.append(f"  Min/Max Latency: {latency['min_latency_ms']:.1f} / {latency['max_latency_ms']:.1f} ms")
                report_lines.append(f"  95th Percentile: {latency['p95_latency_ms']:.1f} ms")
                report_lines.append(f"  99th Percentile: {latency['p99_latency_ms']:.1f} ms")
                report_lines.append(f"  Standard Deviation: {latency['std_latency_ms']:.1f} ms")
            else:
                report_lines.append(f"\nLATENCY ANALYSIS:")
                report_lines.append(f"  No latency measurements available")
            
            # Error analysis
            errors = analysis['errors']
            report_lines.append(f"\nERROR ANALYSIS:")
            report_lines.append(f"  Total Errors: {errors['total_errors']}")
            report_lines.append(f"  Error Rate: {errors['error_rate_percent']:.3f}%")
            if errors['error_types']:
                report_lines.append(f"  Error Types:")
                for error_type, count in errors['error_types'].items():
                    report_lines.append(f"    {error_type.title()}: {count}")
            
            # Performance assessment
            assessment = analysis['assessment']
            report_lines.append(f"\nPERFORMANCE ASSESSMENT:")
            report_lines.append(f"  Overall Grade: {assessment['overall_grade']}")
            report_lines.append(f"  Latency Grade: {assessment['latency_grade']}")
            report_lines.append(f"  Throughput Grade: {assessment['throughput_grade']}")
            report_lines.append(f"  Reliability Grade: {assessment['reliability_grade']}")
            
            if assessment['recommendations']:
                report_lines.append(f"\nRECOMMENDATIONS:")
                for rec in assessment['recommendations']:
                    report_lines.append(f"  - {rec}")
            
            report_lines.append("\n" + "=" * 80)
            
            report_text = "\n".join(report_lines)
            
            if save_to_file:
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                report_file = os.path.join(ANALYSIS_DIR, f'analysis_report_{session_id}_{timestamp}.txt')
                
                with open(report_file, 'w', encoding='utf-8') as f:
                    f.write(report_text)
                
                print(f"\nReport saved to: {report_file}")
            
            return report_text
            
        except Exception as e:
            logger.error(f"Error generating report: {e}")
            raise

def main():
    """Enhanced main function with new commands"""
    
    if len(sys.argv) <= 1:
        print("\nUSAGE:")
        print("=" * 70)
        print("python script.py <command> [options]")
        print("\nCommands:")
        print("  single [duration]           - Single client test (default: 60s)")
        print("  fixed <clients> <duration>  - Fixed client count test")
        print("  scaling [duration]          - Scaling stress test (default: 300s)")
        print("  analyze [session_id]        - Analyze collected data")
        print("  list                        - List available test sessions")
        print("\nExamples:")
        print("  python script.py single 30")
        print("  python script.py fixed 10 120      # 10 clients for 120 seconds")
        print("  python script.py scaling 600")
        print("  python script.py analyze session_id_12345")
        print("  python script.py list")
        print("=" * 70)
        return
    
    command = sys.argv[1]
    
    try:
        if command == "single":
            duration = float(sys.argv[2]) if len(sys.argv) > 2 else 60
            print(f"Running single client test for {duration}s...")
            
            # Create simple data logger for single test
            test_session_id = f"single_{int(duration)}s_{int(time.time())}"
            data_logger = DataLogger(test_session_id)
            stats_collector = SimpleStatsCollector()
            
            asyncio.run(data_collecting_websocket_client("test_client", data_logger, stats_collector, AUDIO_FILE_PATH, duration))
            print("Single client test completed!")
            print(f"Data saved with session ID: {test_session_id}")
            
        elif command == "fixed":
            if len(sys.argv) < 4:
                print("Usage: python script.py fixed <num_clients> <duration_seconds>")
                print("Example: python script.py fixed 10 120")
                return
                
            num_clients = int(sys.argv[2])
            duration = float(sys.argv[3])
            
            if num_clients < 1 or num_clients > 200:
                print("Number of clients must be between 1 and 200")
                return
            
            if duration < 10 or duration > 3600:
                print("Duration must be between 10 and 3600 seconds")
                return
            
            orchestrator = TestOrchestrator()
            orchestrator.run_fixed_client_test(num_clients, duration)
            
        elif command == "scaling":
            duration = float(sys.argv[2]) if len(sys.argv) > 2 else 300
            orchestrator = TestOrchestrator()
            orchestrator.run_scaling_stress_test(duration)
            
        elif command == "analyze":
            analyzer = DataAnalyzer()
            
            if len(sys.argv) > 2:
                session_id = sys.argv[2]
            else:
                sessions = analyzer.list_available_sessions()
                if not sessions:
                    print("No test sessions found.")
                    return
                
                print("Available sessions:")
                for i, session in enumerate(sessions, 1):
                    print(f"  {i}. {session}")
                
                try:
                    choice = int(input("\nSelect session number: ")) - 1
                    if 0 <= choice < len(sessions):
                        session_id = sessions[choice]
                    else:
                        print("Invalid selection")
                        return
                except (ValueError, KeyboardInterrupt):
                    print("Cancelled")
                    return
            
            print(f"\nAnalyzing session: {session_id}")
            report = analyzer.generate_report(session_id)
            print(report)
            
        elif command == "list":
            analyzer = DataAnalyzer()
            sessions = analyzer.list_available_sessions()
            
            if sessions:
                print("\nAvailable test sessions:")
                print("=" * 50)
                for session in sessions:
                    # Parse session info from ID
                    parts = session.split('_')
                    if len(parts) >= 3:
                        test_type = parts[0]
                        if test_type == 'fixed':
                            clients = parts[1].rstrip('c')
                            duration = parts[2].rstrip('s')
                            timestamp = parts[3] if len(parts) > 3 else 'unknown'
                            print(f"  {session}")
                            print(f"    Type: Fixed ({clients} clients, {duration}s)")
                        elif test_type == 'scaling':
                            duration = parts[1].rstrip('s')
                            timestamp = parts[2] if len(parts) > 2 else 'unknown'
                            print(f"  {session}")
                            print(f"    Type: Scaling ({duration}s)")
                        else:
                            print(f"  {session}")
                    else:
                        print(f"  {session}")
                print("=" * 50)
                print(f"Total: {len(sessions)} sessions")
                print("\nUse 'python script.py analyze <session_id>' to analyze a specific session")
            else:
                print("No test sessions found.")
                print("Run some tests first using 'single', 'fixed', or 'scaling' commands.")
            
        else:
            print(f"Unknown command: {command}")
            print("Available commands: single, fixed, scaling, analyze, list")
            
    except Exception as e:
        logger.error(f"Command execution error: {e}")
        print(f"Error: {e}")

if __name__ == "__main__":
    main()