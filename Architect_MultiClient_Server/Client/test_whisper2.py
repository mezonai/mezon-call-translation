import asyncio
import websockets
import sounddevice as sd
import numpy as np
import json
import sys
import threading

CHUNK = 1024
RATE = 16000
CHANNELS = 1
DTYPE = 'int16'

CLIENT_ID = "doanht"
SESSION_ID = "room2"
TRANSCRIPT = True
TRANSLATION = False
LANGUAGE = "en"

mic_enabled = False  # trạng thái bật/tắt mic


async def send_audio():
    global mic_enabled

    uri = (
        f"ws://localhost:8000/ws/faster-whisper/"
        f"?client_id={CLIENT_ID}&session_id={SESSION_ID}"
        f"&transcript={str(TRANSCRIPT).lower()}"
        f"&translation={str(TRANSLATION).lower()}"
        f"&language={str(LANGUAGE).lower()}"
    )

    async with websockets.connect(uri) as websocket:
        print("Connected to server")

        async def receive():
            async for message in websocket:
                print(f"{message}")

        recv_task = asyncio.create_task(receive())

        def audio_generator():
            with sd.InputStream(
                samplerate=RATE, channels=CHANNELS, dtype=DTYPE, blocksize=CHUNK
            ) as stream:
                while True:
                    audio_chunk, _ = stream.read(CHUNK)
                    yield audio_chunk

        async def stream_audio():
            for chunk in audio_generator():
                if mic_enabled:  # chỉ gửi khi mic bật
                    await websocket.send(chunk.tobytes())
                await asyncio.sleep(0)  # nhường event loop

        audio_task = asyncio.create_task(stream_audio())

        # theo dõi phím bấm Ctrl+I
        def key_listener():
            global mic_enabled
            if sys.platform == "win32":
                import msvcrt
                while True:
                    if msvcrt.kbhit():
                        ch = msvcrt.getch()
                        if ch == b'\t':  # Ctrl+I = TAB
                            mic_enabled = not mic_enabled
                            print("🎤 Mic", "ON" if mic_enabled else "OFF")
            else:
                import keyboard
                while True:
                    keyboard.wait("ctrl+i")
                    mic_enabled = not mic_enabled
                    print("🎤 Mic", "ON" if mic_enabled else "OFF")

        threading.Thread(target=key_listener, daemon=True).start()

        try:
            await audio_task
        except asyncio.CancelledError:
            pass
        finally:
            recv_task.cancel()


if __name__ == "__main__":
    asyncio.run(send_audio())
