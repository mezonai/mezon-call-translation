import asyncio
import websockets
import numpy as np
from faster_whisper import WhisperModel

# Cấu hình model Whisper
model = WhisperModel("base", device="cpu", compute_type="int8")

# Cấu hình audio
SAMPLE_RATE = 16000
CHUNK_DURATION = 1  # giây
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # bytes (vì 16-bit = 2 bytes)

# Xử lý 1 kết nối WebSocket
async def handle_connection(websocket):
    print("🔌 Client connected")
    buffer = bytearray()

    try:
        async for message in websocket:
            buffer.extend(message)

            while len(buffer) >= CHUNK_SIZE:
                chunk = buffer[:CHUNK_SIZE]
                buffer = buffer[CHUNK_SIZE:]

                # Chuyển sang numpy float32
                audio_data = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0

                # Gọi Whisper để transcribe
                
                segments, _ = model.transcribe(audio_data, language="en", beam_size=1)

                text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])

                await websocket.send(text)

    except websockets.exceptions.ConnectionClosed:
        print("❌ Client disconnected")

# Chạy server WebSocket
async def main():
    async with websockets.serve(handle_connection, "0.0.0.0", 8765):
        print("🚀 Server listening on ws://0.0.0.0:8765")
        await asyncio.Future()  # giữ server chạy mãi

if __name__ == "__main__":
    asyncio.run(main())
