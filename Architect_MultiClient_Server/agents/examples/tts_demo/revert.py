import soundfile as sf
import struct
import time
import statistics
import json
import sqlite3
from vosk import Model, KaldiRecognizer
from tabulate import tabulate
from tqdm import tqdm
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import queue
from dataclasses import dataclass
from typing import Optional

# --- Config ---
MODEL_PATH = r"E:\NCC\mezon-call-translation\model\transcription\en-model"
AUDIO_PATH = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Client\mp3cut.wav"
OUTPUT_FILE = Path("vosk_test_result2s.txt")
DB_FILE = Path("vosk_transcription2s.db")

CHUNK_MS = [100, 200, 300, 400]
NUM_CLIENTS = 5
NUM_WORKERS = 4  # Số worker threads xử lý Vosk
BUFFER_SIZE = 50  # Kích thước queue buffer cho mỗi worker

# Thread-local storage cho database connections
thread_local = threading.local()
db_lock = threading.Lock()

@dataclass
class AudioChunk:
    """Class để đóng gói audio chunk data"""
    client_id: int
    chunk_ms: int
    chunk_data: bytes
    chunk_index: int
    timestamp_ms: float
    total_chunks: int
    result_queue: queue.Queue  # Queue để nhận kết quả
    
@dataclass
class ProcessResult:
    """Class để đóng gói kết quả xử lý"""
    client_id: int
    chunk_ms: int
    chunk_index: int
    timestamp_ms: float
    text: str
    is_final: bool
    process_time_ms: float

def get_db_connection():
    """Lấy database connection cho thread hiện tại"""
    if not hasattr(thread_local, 'conn'):
        with db_lock:
            thread_local.conn = sqlite3.connect(DB_FILE)
            thread_local.conn.execute("PRAGMA synchronous = NORMAL")
            thread_local.conn.execute("PRAGMA cache_size = 10000")
            thread_local.conn.execute("PRAGMA temp_store = MEMORY")
            thread_local.conn.execute("PRAGMA journal_mode = WAL")
            thread_local.conn.execute("PRAGMA wal_autocheckpoint = 1000")
            thread_local.conn.execute("PRAGMA busy_timeout = 30000")
    return thread_local.conn

def close_thread_db():
    """Đóng database connection của thread hiện tại"""
    if hasattr(thread_local, 'conn'):
        thread_local.conn.close()
        del thread_local.conn

class VoskWorker:
    """Worker thread để xử lý Vosk recognition"""
    
    def __init__(self, worker_id: int, model: 'Model', samplerate: int):
        self.worker_id = worker_id
        self.model = model
        self.samplerate = samplerate
        self.input_queue = queue.Queue(maxsize=BUFFER_SIZE)
        self.is_running = threading.Event()
        self.is_running.set()
        self.thread = None
        
        # Tạo recognizer riêng cho worker này
        self.recognizer = KaldiRecognizer(model, samplerate)
        
        # Statistics
        self.processed_chunks = 0
        self.total_process_time = 0.0
        
    def start(self):
        """Start worker thread"""
        self.thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.thread.start()
        
    def stop(self):
        """Stop worker thread"""
        self.is_running.clear()
        # Đưa None vào queue để signal stop
        try:
            self.input_queue.put(None, timeout=1.0)
        except queue.Full:
            pass
            
    def submit_chunk(self, audio_chunk: AudioChunk, timeout: float = 1.0) -> bool:
        """Submit audio chunk to worker queue"""
        try:
            self.input_queue.put(audio_chunk, timeout=timeout)
            return True
        except queue.Full:
            return False
            
    def _worker_loop(self):
        """Main worker loop"""
        print(f"Worker {self.worker_id} started")
        
        try:
            while self.is_running.is_set():
                try:
                    # Get chunk from queue
                    chunk = self.input_queue.get(timeout=0.5)
                    
                    if chunk is None:  # Stop signal
                        break
                        
                    # Process chunk
                    start_time = time.perf_counter()
                    
                    # Reset recognizer for new client/config if needed
                    if chunk.chunk_index == 0:
                        self.recognizer = KaldiRecognizer(self.model, self.samplerate)
                    
                    is_final = self.recognizer.AcceptWaveform(chunk.chunk_data)
                    elapsed_ms = (time.perf_counter() - start_time) * 1000
                    
                    # Get result text
                    text = ""
                    if is_final:
                        result = json.loads(self.recognizer.Result())
                        text = result.get("text", "")
                    else:
                        result = json.loads(self.recognizer.PartialResult())
                        text = result.get("partial", "")
                    
                    # Create result
                    process_result = ProcessResult(
                        client_id=chunk.client_id,
                        chunk_ms=chunk.chunk_ms,
                        chunk_index=chunk.chunk_index,
                        timestamp_ms=chunk.timestamp_ms,
                        text=text.strip(),
                        is_final=is_final,
                        process_time_ms=elapsed_ms
                    )
                    
                    # Send result back
                    chunk.result_queue.put(process_result)
                    
                    # Update statistics
                    self.processed_chunks += 1
                    self.total_process_time += elapsed_ms
                    
                    # Mark task as done
                    self.input_queue.task_done()
                    
                except queue.Empty:
                    continue  # Timeout, check if still running
                except Exception as e:
                    print(f"Worker {self.worker_id} error: {e}")
                    
        except Exception as e:
            print(f"Worker {self.worker_id} fatal error: {e}")
        finally:
            print(f"Worker {self.worker_id} stopped")

class WorkerPool:
    """Pool of Vosk workers with load balancing"""
    
    def __init__(self, num_workers: int, model: 'Model', samplerate: int):
        self.num_workers = num_workers
        self.workers = []
        self.current_worker = 0  # Round-robin load balancing
        self.lock = threading.Lock()
        
        # Create workers
        for i in range(num_workers):
            worker = VoskWorker(i, model, samplerate)
            self.workers.append(worker)
            worker.start()
            
    def submit_chunk(self, audio_chunk: AudioChunk, timeout: float = 1.0) -> bool:
        """Submit chunk to least busy worker (round-robin)"""
        with self.lock:
            start_worker = self.current_worker
            
            # Try round-robin assignment
            for _ in range(self.num_workers):
                worker = self.workers[self.current_worker]
                self.current_worker = (self.current_worker + 1) % self.num_workers
                
                if worker.submit_chunk(audio_chunk, timeout=0.1):  # Quick timeout
                    return True
                    
            # If all workers busy, try with longer timeout
            worker = self.workers[start_worker]
            return worker.submit_chunk(audio_chunk, timeout=timeout)
    
    def stop_all(self):
        """Stop all workers"""
        for worker in self.workers:
            worker.stop()
            
        # Wait for workers to finish
        for worker in self.workers:
            if worker.thread:
                worker.thread.join(timeout=2.0)
    
    def get_statistics(self):
        """Get worker pool statistics"""
        total_processed = sum(w.processed_chunks for w in self.workers)
        total_time = sum(w.total_process_time for w in self.workers)
        avg_time = total_time / total_processed if total_processed > 0 else 0
        
        worker_stats = []
        for w in self.workers:
            worker_stats.append({
                'worker_id': w.worker_id,
                'processed': w.processed_chunks,
                'avg_time': w.total_process_time / w.processed_chunks if w.processed_chunks > 0 else 0,
                'queue_size': w.input_queue.qsize()
            })
        
        return {
            'total_processed': total_processed,
            'avg_process_time': avg_time,
            'worker_details': worker_stats
        }

# --- Load model ---
print("Loading model...")
model = Model(MODEL_PATH)
print("Model loaded")

# --- Load audio ---
print("Loading audio...")
data, samplerate = sf.read(AUDIO_PATH, dtype="int16")
assert samplerate == 16000, "Audio phải là 16kHz"
audio_duration = len(data) / samplerate
print(f"Audio length: {audio_duration:.2f}s")

# --- SQLite setup ---
main_conn = sqlite3.connect(DB_FILE)
main_cursor = main_conn.cursor()
main_cursor.execute("""
CREATE TABLE IF NOT EXISTS transcriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client INTEGER,
    chunk_ms INTEGER,
    time_audio_ms REAL,
    process_time_ms REAL,
    text TEXT,
    final INTEGER,
    worker_id INTEGER
)
""")
main_conn.commit()
main_conn.close()

# --- Results storage ---
summary = []
output_lines = []

def test_chunk_with_workers(ms: int, client_id: int, worker_pool: WorkerPool):
    """Test chunk processing through worker pool"""
    try:
        frame_size = int(samplerate * (ms / 1000.0))
        total_chunks = len(data) // frame_size + (1 if len(data) % frame_size else 0)
        
        # Result queue cho client này
        result_queue = queue.Queue()
        
        # Database connection
        conn = get_db_connection()
        cursor = conn.cursor()
        
        i = 0
        chunk_count = 0
        total_start = time.perf_counter()
        timings = []
        
        # Progress bar
        with tqdm(total=total_chunks, desc=f"C{client_id}-{ms}ms", ncols=90, 
                  position=client_id * len(CHUNK_MS) + CHUNK_MS.index(ms), leave=True) as pbar:
            
            # Submit all chunks to workers
            submitted_chunks = []
            while i < len(data):
                chunk = data[i:i+frame_size]
                if len(chunk) == 0:
                    break
                
                # Padding if needed
                if len(chunk) < frame_size and i + frame_size >= len(data):
                    chunk = list(chunk) + [0] * (frame_size - len(chunk))
                
                pcm_data = struct.pack("<" + str(len(chunk)) + "h", *chunk)
                time_ms = (i / samplerate) * 1000
                
                # Create audio chunk
                audio_chunk = AudioChunk(
                    client_id=client_id,
                    chunk_ms=ms,
                    chunk_data=pcm_data,
                    chunk_index=chunk_count,
                    timestamp_ms=time_ms,
                    total_chunks=total_chunks,
                    result_queue=result_queue
                )
                
                # Submit to worker pool với retry
                submitted = False
                retry_count = 0
                while not submitted and retry_count < 3:
                    submitted = worker_pool.submit_chunk(audio_chunk, timeout=1.0)
                    if not submitted:
                        time.sleep(0.1 * (retry_count + 1))
                        retry_count += 1
                
                if submitted:
                    submitted_chunks.append(chunk_count)
                else:
                    print(f"Failed to submit chunk {chunk_count} for client {client_id}")
                
                i += frame_size
                chunk_count += 1
                
            # Collect results
            batch_data = []
            batch_size = 100
            received_results = 0
            
            while received_results < len(submitted_chunks):
                try:
                    result = result_queue.get(timeout=10.0)  # 10s timeout
                    timings.append(result.process_time_ms)
                    
                    # Save to database if text is not empty
                    if result.text:
                        batch_data.append((
                            result.client_id, result.chunk_ms, result.timestamp_ms,
                            result.process_time_ms, result.text, 
                            1 if result.is_final else 0, -1  # worker_id not tracked per chunk
                        ))
                        
                        # Batch insert with retry logic
                        if len(batch_data) >= batch_size:
                            retry_count = 0
                            while retry_count < 3:
                                try:
                                    cursor.executemany("""
                                    INSERT INTO transcriptions (client, chunk_ms, time_audio_ms, 
                                                               process_time_ms, text, final, worker_id)
                                    VALUES (?, ?, ?, ?, ?, ?, ?)
                                    """, batch_data)
                                    conn.commit()
                                    break
                                except sqlite3.OperationalError as e:
                                    if "database is locked" in str(e) and retry_count < 2:
                                        time.sleep(0.1 * (retry_count + 1))
                                        retry_count += 1
                                    else:
                                        raise
                            batch_data = []
                    
                    received_results += 1
                    pbar.update(1)
                    
                except queue.Empty:
                    print(f"Timeout waiting for results from client {client_id}")
                    break
            
            # Insert remaining batch data
            if batch_data:
                retry_count = 0
                while retry_count < 3:
                    try:
                        cursor.executemany("""
                        INSERT INTO transcriptions (client, chunk_ms, time_audio_ms, 
                                                   process_time_ms, text, final, worker_id)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, batch_data)
                        conn.commit()
                        break
                    except sqlite3.OperationalError as e:
                        if "database is locked" in str(e) and retry_count < 2:
                            time.sleep(0.1 * (retry_count + 1))
                            retry_count += 1
                        else:
                            raise
        
        total_elapsed = time.perf_counter() - total_start
        
        # Calculate statistics
        if timings:
            avg_time = statistics.mean(timings)
            min_time = min(timings)
            max_time = max(timings)
            median_time = statistics.median(timings)
            stdev_time = statistics.stdev(timings) if len(timings) > 1 else 0.0
            p95_time = statistics.quantiles(timings, n=20)[18] if len(timings) > 20 else max_time
            p99_time = statistics.quantiles(timings, n=100)[98] if len(timings) > 100 else max_time
        else:
            avg_time = min_time = max_time = median_time = stdev_time = p95_time = p99_time = 0.0
        
        rtf = total_elapsed / audio_duration
        
        return {
            "client": client_id,
            "chunk_ms": ms,
            "chunks": chunk_count,
            "total_time": total_elapsed,
            "avg_time": avg_time,
            "median_time": median_time,
            "min_time": min_time,
            "max_time": max_time,
            "stdev_time": stdev_time,
            "p95_time": p95_time,
            "p99_time": p99_time,
            "rtf": rtf,
            "audio_duration": audio_duration,
            "submitted_chunks": len(submitted_chunks),
            "received_results": received_results
        }
        
    except Exception as e:
        print(f"Error in test_chunk (client {client_id}, chunk {ms}ms): {e}")
        return None
    finally:
        close_thread_db()

# --- Main execution ---
print(f"Starting test with {NUM_CLIENTS} clients, {NUM_WORKERS} workers, buffer size {BUFFER_SIZE}")
print(f"Chunk sizes: {CHUNK_MS}")
print("=" * 80)

# Create worker pool
worker_pool = WorkerPool(NUM_WORKERS, model, samplerate)

try:
    # Run all clients concurrently
    with ThreadPoolExecutor(max_workers=NUM_CLIENTS * len(CHUNK_MS)) as executor:
        futures = []
        for cid in range(NUM_CLIENTS):
            for ms in CHUNK_MS:
                futures.append(executor.submit(test_chunk_with_workers, ms, cid, worker_pool))

        for f in as_completed(futures):
            result = f.result()
            if result:
                summary.append(result)
                text = (f"[Client {result['client']} | {result['chunk_ms']}ms] "
                        f"Chunks={result['chunks']} | Total={result['total_time']:.2f}s | "
                        f"RTF={result['rtf']:.3f} | "
                        f"Avg={result['avg_time']:.2f}ms | Submitted/Received={result['submitted_chunks']}/{result['received_results']}")
                print(text)
                output_lines.append(text)

finally:
    # Stop worker pool
    worker_pool.stop_all()

print("\n" + "=" * 80)

# Worker pool statistics
worker_stats = worker_pool.get_statistics()
print("===== Worker Pool Statistics =====")
print(f"Total chunks processed: {worker_stats['total_processed']}")
print(f"Average process time: {worker_stats['avg_process_time']:.2f}ms")

worker_table = [["Worker", "Processed", "Avg Time(ms)", "Final Queue Size"]]
for w_stat in worker_stats['worker_details']:
    worker_table.append([
        w_stat['worker_id'], w_stat['processed'], 
        f"{w_stat['avg_time']:.2f}", w_stat['queue_size']
    ])
worker_stats_str = tabulate(worker_table, headers="firstrow", tablefmt="grid")
print(worker_stats_str)
output_lines.append("\n===== Worker Pool Statistics =====\n")
output_lines.append(worker_stats_str + "\n")

# Client results summary
if summary:
    summary_table = [["Client", "Chunk(ms)", "Chunks", "Total(s)", "RTF", "Avg(ms)", 
                     "Median(ms)", "P95(ms)", "Max(ms)", "Success Rate"]]
    
    summary.sort(key=lambda x: (x['client'], x['chunk_ms']))
    
    for s in summary:
        success_rate = (s['received_results'] / s['submitted_chunks']) * 100 if s['submitted_chunks'] > 0 else 0
        summary_table.append([
            s['client'], s['chunk_ms'], s['chunks'], f"{s['total_time']:.2f}",
            f"{s['rtf']:.3f}", f"{s['avg_time']:.2f}", f"{s['median_time']:.2f}",
            f"{s['p95_time']:.2f}", f"{s['max_time']:.2f}", f"{success_rate:.1f}%"
        ])
    
    summary_str = tabulate(summary_table, headers="firstrow", tablefmt="grid")
    print("===== Client Results Summary =====")
    print(summary_str)
    output_lines.append("\n===== Client Results Summary =====\n")
    output_lines.append(summary_str + "\n")

# Write results
try:
    OUTPUT_FILE.write_text("\n".join(output_lines), encoding="utf-8")
    print(f"\n>> Results written to {OUTPUT_FILE.absolute()}")
    print(f">> Transcriptions saved to SQLite DB: {DB_FILE.absolute()}")
except Exception as e:
    print(f"Error writing output file: {e}")

print(f"\n>> Test completed successfully!")
print(f">> Architecture: {NUM_CLIENTS} clients -> {NUM_WORKERS} workers (buffer size: {BUFFER_SIZE})")
print(f">> Total configurations tested: {len(summary)}")
print(f">> Database records can be queried with SQL for detailed analysis")