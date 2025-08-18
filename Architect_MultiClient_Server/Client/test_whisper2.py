import asyncio
import websockets
import sounddevice as sd
import numpy as np
import json

CHUNK = 1024
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'


CLIENT_ID = "grrrr" # này có thể là username hoặc userid của người tham gia cuộc họp
SESSION_ID = "room2" # này là id phòng chat nhé.
TRANSCRIPT = False  # này là nếu người dùng enable transcript thì sẽ True nhá
TRANSLATION = True
LANGUAGE = "ja"

async def send_audio():
    uri = f"ws://localhost:8000/ws/faster-whisper/?client_id={CLIENT_ID}&session_id={SESSION_ID}&transcript={str(TRANSCRIPT).lower()}&translation={str(TRANSLATION).lower()}&language={str(LANGUAGE).lower()}"
    async with websockets.connect(uri) as websocket:
        print("Connected to server")

       
        async def receive():
            async for message in websocket:
                print(f"{message}")
                #  thực ra đoạn này muốn hiển thị theo tên thì có thể chỉ nhận userid thôi, sau đó join sang mezon để lấy tên nhá chứ ít khi gửi cả cái username hay email lắm ae

        recv_task = asyncio.create_task(receive())

        # cái này là khi mình stream âm thanh liên tục, cứ được một chunk ( tầm bao nhiêu mẫu dữ liệu đấy) thì ae gửi lên server, ae có thể tùy chỉnh
        def audio_generator():
            with sd.InputStream(samplerate=RATE, channels=CHANNELS, dtype=DTYPE, blocksize=CHUNK) as stream:
                while True:
                    audio_chunk, _ = stream.read(CHUNK)
                    yield audio_chunk

        try:
            for chunk in audio_generator():
                # ae mình mặc định gửi dưới dạng bytes (raw PCM 16bit)
                await websocket.send(chunk.tobytes())
               
                # if np.random.rand() < 0.01:  # Ngẫu nhiên gửi lệnh bật/tắt transcript
                #     msg = json.dumps({"action": "set_transcript", "value": True})
                #     await websocket.send(msg)
        except KeyboardInterrupt:
            pass
        finally:
            recv_task.cancel()

if __name__ == "__main__":
    asyncio.run(send_audio())