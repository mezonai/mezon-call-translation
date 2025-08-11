import asyncio
import websockets
import sounddevice as sd
import numpy as np
import json

# Chạy trên máy khai.hoangdo
CHUNK = 1024
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

# Thông tin client và session
CLIENT_ID = "khaidohoang"
SESSION_ID = "room1"
TRANSCRIPT = True  # Mặc định khi connect

async def send_audio():
    uri = f"ws://localhost:8000/ws/vosk/?client_id={CLIENT_ID}&session_id={SESSION_ID}&transcript={str(TRANSCRIPT).lower()}"
    async with websockets.connect(uri) as websocket:
        print("Connected to server")

        # Task nhận text từ server
        async def receive():
            async for message in websocket:
                print(f"{message}")

        recv_task = asyncio.create_task(receive())

        # Generator thu âm audio theo từng chunk
        def audio_generator():
            with sd.InputStream(samplerate=RATE, channels=CHANNELS, dtype=DTYPE, blocksize=CHUNK) as stream:
                while True:
                    audio_chunk, _ = stream.read(CHUNK)
                    yield audio_chunk

        try:
            for chunk in audio_generator():
                # Gửi dưới dạng bytes (raw PCM 16bit)
                await websocket.send(chunk.tobytes())
                await asyncio.sleep(CHUNK / RATE)
                # Ví dụ: sau 5 chunk thì bật transcript
                # (Bạn có thể thay bằng input hoặc logic khác)
                # if np.random.rand() < 0.01:  # Ngẫu nhiên gửi lệnh bật/tắt transcript
                #     msg = json.dumps({"action": "set_transcript", "value": True})
                #     await websocket.send(msg)
        except KeyboardInterrupt:
            pass
        finally:
            recv_task.cancel()

if __name__ == "__main__":
    asyncio.run(send_audio())