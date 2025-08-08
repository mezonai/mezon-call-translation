import asyncio
import websockets
import numpy as np
from faster_whisper import WhisperModel
import threading
import queue

# Load Whisper model (ở ngoài để chia sẻ cho các luồng)
model = WhisperModel("base", device="cpu", compute_type="int8")

SAMPLE_RATE = 16000
CHUNK_DURATION = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # 16-bit PCM

# Hàm xử lý STT trong thread riêng
def stt_worker(audio_queue, result_queue, stop_event):
    while not stop_event.is_set():
        try:
            chunk, websocket = audio_queue.get(timeout=0.5)
            audio_data = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0

            segments, _ = model.transcribe(audio_data, language="en", beam_size=1)
            text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])

            if text:
                result_queue.put((text, websocket))

        except queue.Empty:
            continue

# Hàm WebSocket xử lý 1 client
async def handle_connection(websocket):
    print("🔌 Client connected")
    buffer = bytearray()

    try:    
        while True:
            message = await websocket.recv()
            buffer.extend(message)

            while len(buffer) >= CHUNK_SIZE:
                chunk = buffer[:CHUNK_SIZE]
                buffer = buffer[CHUNK_SIZE:]

                audio_queue.put((chunk, websocket))

    except websockets.exceptions.ConnectionClosed:
        print("❌ Client disconnected")

# Gửi kết quả từ STT cho client (kênh riêng)
async def result_dispatcher():
    while True:
        try:
            text, websocket = result_queue.get_nowait()
            await websocket.send(text)
        except queue.Empty:
            await asyncio.sleep(0.01)

# Khởi chạy server
async def main():
    asyncio.create_task(result_dispatcher())

    async with websockets.serve(handle_connection, "0.0.0.0", 8765):
        print("🚀 Server listening on ws://0.0.0.0:8765")
        await asyncio.Future()

if __name__ == "__main__":
    audio_queue = queue.Queue()
    result_queue = queue.Queue()
    stop_event = threading.Event()

    stt_thread = threading.Thread(target=stt_worker, args=(audio_queue, result_queue, stop_event), daemon=True)
    stt_thread.start()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("🛑 Shutting down...")
        stop_event.set()
        stt_thread.join()
