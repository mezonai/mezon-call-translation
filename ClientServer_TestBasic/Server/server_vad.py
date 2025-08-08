import asyncio
import websockets
import numpy as np
import torch
import struct
from vad import VoiceActivityDetector
from faster_whisper import WhisperModel  # bạn có thể đổi sang model khác

vad_model = VoiceActivityDetector()
model = WhisperModel("base", device="cpu", compute_type="int8")  # model nhanh, có thể đổi

clients = set()

async def handle_connection(websocket):
    print("Client connected")
    clients.add(websocket)

    buffer = bytes()
    sample_rate = 16000
    chunk_duration = 1  
    chunk_size = int(sample_rate * chunk_duration) * 2  # 16bit = 2 bytes

    vad_triggered = False
    speech_buffer = bytes()

    try:
        async for message in websocket:
            buffer += message

            while len(buffer) >= chunk_size:
                chunk = buffer[:chunk_size]
                buffer = buffer[chunk_size:]

                audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0

                triggered = vad_model.is_speech(audio_np, sample_rate=sample_rate)

                if triggered:
                    vad_triggered = True
                    speech_buffer += chunk
                elif vad_triggered:
                    vad_triggered = False

                    audio_array = np.frombuffer(speech_buffer, dtype=np.int16).astype(np.float32) / 32768.0
                    segments, _ = model.transcribe(audio_array, language='en', beam_size=1)

                    text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])

                   

                    await websocket.send(text)
                    speech_buffer = bytes()

    except websockets.exceptions.ConnectionClosed:
        print("Client disconnected")
    finally:
        clients.remove(websocket)

async def main():
    async with websockets.serve(handle_connection, "0.0.0.0", 8765):
        print("Server listening on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever

if __name__ == "__main__":
    asyncio.run(main())
