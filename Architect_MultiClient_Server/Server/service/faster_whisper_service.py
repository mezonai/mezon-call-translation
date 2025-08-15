import threading
import queue
import numpy as np
from faster_whisper import WhisperModel
from utils.vad import SileroVAD
import time
import logging
import sys
import wave
import os
from datetime import datetime

SAMPLE_RATE = 16000
CHUNK_DURATION = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # 16-bit PCM
# OVERLAP_SIZE = int(CHUNK_SIZE * 0.25)  # 10% overlap

# ===== Logger Setup =====
class LogFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[94m",   # Blue
        "INFO": "\033[92m",    # Green
        "WARNING": "\033[93m", # Yellow
        "ERROR": "\033[91m",   # Red
        "CRITICAL": "\033[95m" # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        record.levelname = f"{color}{record.levelname}{self.RESET}"
        return super().format(record)

log = logging.getLogger("STTFasterWhisper")
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(LogFormatter(
    "[%(asctime)s] [%(threadName)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
))
log.addHandler(handler)
log.setLevel(logging.DEBUG)
# =========================

class STTFasterWhisperService:
    def __init__(self):
        log.info("Initializing Whisper model (medium, float16, cuda)...")
        self.model = WhisperModel("medium", device="cuda", compute_type="float16")
        
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()

        # VAD setup
        log.info("Initializing SileroVAD...")
        self.vad = SileroVAD()
        self.vad_trigger = False
        self.speech_buffer = bytes()
        self.buffer_count = 0

        self.thread = threading.Thread(target=self.stt_worker, daemon=True, name="STT-Worker")
        self.thread.start()
        log.info("STT worker thread started.")

    def stt_worker(self):
        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.1)
                log.debug(f"Received chunk from client={client_id}, session={session_id}, size={len(chunk)} bytes")

                # Convert chunk bytes to float32 normalized audio
                audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                triggered = self.vad.is_speech(audio_np, sample_rate=SAMPLE_RATE)
                log.debug(f"VAD triggered={triggered}")

                if triggered:
                    self.vad_trigger = True
                    self.speech_buffer += chunk
                    self.buffer_count += 1
                    log.debug(f"Speech buffer length={len(self.speech_buffer)} bytes")
                    print("buffer count:", self.buffer_count)
                    if len(self.speech_buffer) == 0:
                        continue
                    if(self.buffer_count == 1): 
                        continue
                    # Chuyển bytes -> float32 để transcribe
                    audio_array = np.frombuffer(self.speech_buffer, dtype=np.int16).astype(np.float32) / 32768.0
                    
                    # GHI RA FILE WAV
                    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".wav"
                    filepath = os.path.join("recordings", filename)
                    os.makedirs("recordings", exist_ok=True)  # tạo thư mục nếu chưa có
                    with wave.open(filepath, 'wb') as wf:
                        wf.setnchannels(1)  # mono
                        wf.setsampwidth(2)  # 16-bit PCM = 2 bytes
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(self.speech_buffer)
                    log.debug(f"Saved audio chunk to {filepath}")

                    # Nhận diện
                    time_start = time.time()
                    segments, _ = self.model.transcribe(audio_array, beam_size=1, language="vi")
                    text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])
                    time_end = time.time()

                    if text:
                        log.info(f"Transcription (client={client_id}, session={session_id}): {text}")
                        log.debug(f"Processing time: {time_end - time_start:.2f}s")
                        print(text)
                        if(self.buffer_count == 2 and len(text) > 30):
                            print("ảo")
                        else:
                            self.result_queue.put((text, client_id, session_id))
                elif self.vad_trigger:
                    log.debug(f"Speech ended for client={client_id}, session={session_id}")
                    self.vad_trigger = False
                    self.speech_buffer = bytes()
                    self.buffer_count = 0
                else:
                    # if len(self.speech_buffer) > OVERLAP_SIZE:
                    #     self.speech_buffer = self.speech_buffer[-OVERLAP_SIZE:]
                    # else:
                    self.speech_buffer = bytes()

            except queue.Empty:
                continue

    def submit_audio(self, chunk, client_id, session_id):
        log.debug(f"Submitting audio chunk for client={client_id}, session={session_id}")
        self.audio_queue.put((chunk, client_id, session_id))

    def get_result_nowait(self):
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self):
        log.info("Shutting down STT service...")
        self.stop_event.set()
        self.thread.join()
        log.info("STT worker stopped.")


stt_service = STTFasterWhisperService()
