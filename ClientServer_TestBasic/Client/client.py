# import asyncio
# import websockets
# import pyaudio

# CHUNK = 1024
# FORMAT = pyaudio.paInt16
# CHANNELS = 1
# RATE = 16000

# async def send_audio():
#     uri = "ws://localhost:8765"
#     async with websockets.connect(uri) as websocket:
#         print("Connected to server")

#         p = pyaudio.PyAudio()
#         stream = p.open(format=FORMAT,
#                         channels=CHANNELS,
#                         rate=RATE,
#                         input=True,
#                         frames_per_buffer=CHUNK)

#         async def receive():
#             async for message in websocket:
#                 print(f"{message}")

#         recv_task = asyncio.create_task(receive())

#         try:
#             while True:
#                 data = stream.read(CHUNK, exception_on_overflow=False)
#                 await websocket.send(data)
#         except KeyboardInterrupt:
#             pass
#         finally:
#             stream.stop_stream()
#             stream.close()
#             p.terminate()
#             recv_task.cancel()

# if __name__ == "__main__":
#     asyncio.run(send_audio())




import asyncio
import websockets
import sounddevice as sd
import numpy as np

CHUNK = 1024
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

async def send_audio():
    uri = "ws://localhost:8765"
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
        except KeyboardInterrupt:
            pass
        finally:
            recv_task.cancel()

if __name__ == "__main__":
    asyncio.run(send_audio())
