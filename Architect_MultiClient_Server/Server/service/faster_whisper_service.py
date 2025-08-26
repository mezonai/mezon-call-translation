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
from session_manager import session_manager
from collections import defaultdict
import torch

NEON_GREEN = '\033[92m'

SAMPLE_RATE = 16000
CHUNK_DURATION = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # 16-bit PCM
# OVERLAP_SIZE = int(CHUNK_SIZE * 0.25)  # 10% overlap
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
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
def default_client():
    return {"last_text": "", "buffer_count": 0, "last_overlap_start_byte": None, "speech_buffer": bytes()}

class STTFasterWhisperService:


    def __init__(self):
        log.info("Initializing Whisper model (medium, float16, cuda)...")
        self.model = WhisperModel("large-v3-turbo", device=DEVICE, compute_type="float16")
        
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.data_client_temp = defaultdict(lambda: defaultdict(default_client))

        # VAD setup
        log.info("Initializing SileroVAD...")
        self.vad = SileroVAD()
        self.vad_trigger = False

        self.thread = threading.Thread(target=self.stt_worker, daemon=True, name="STT-Worker")
        self.thread.start()
        log.info("STT worker thread started.")

    def stt_worker(self):
        # OVERLAP_SEGMENT_COUNT: số lượng segment gần nhất sẽ được giữ lại 
        # để đảm bảo ngữ cảnh không bị mất khi ghép transcript (overlap handling).
        OVERLAP_SEGMENT_COUNT = 3      

        # OVERLAP_MIN_SEGMENT: chỉ áp dụng cơ chế overlap nếu đã có 
        # ít nhất N segment (tránh trường hợp dữ liệu quá ngắn gây lỗi).
        OVERLAP_MIN_SEGMENT = 5


        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.1)

                client_lang = session_manager.get_client_language(session_id, client_id)
                print("ngon ngu: ", client_lang)
                if client_lang:
                    target_language = client_lang
                else:
                    target_language = "en"

                log.debug(f"Received chunk from client={client_id}, session={session_id}, size={len(chunk)} bytes")

                # Convert chunk bytes -> float32 normalized
                audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                triggered = self.vad.is_speech(audio_np, sample_rate=SAMPLE_RATE)
                log.debug(f"VAD triggered={triggered}")

                if triggered:
                    log.info(f"Bắt đầu time trước khi transcibe: {time.time()}")
                    self.vad_trigger = True
                    self.data_client_temp[session_id][client_id]["speech_buffer"] += chunk
                    self.data_client_temp[session_id][client_id]["buffer_count"] += 1
                    print(f"chunk count: {self.data_client_temp[session_id][client_id]["buffer_count"]}")
                    log.debug(f"Speech buffer length={len(self.data_client_temp[session_id][client_id]["speech_buffer"])} bytes")
                    if len(self.data_client_temp[session_id][client_id]["speech_buffer"]) == 0:
                        continue

                    # Chuyển sang mảng float32
                    if self.data_client_temp[session_id][client_id]["last_overlap_start_byte"] is not None:
                        log.debug(f"Applying overlap start at byte {self.data_client_temp[session_id][client_id]["last_overlap_start_byte"]}")
                        # self.data_client_temp[session_id][client_id]["speech_buffer"] = self.data_client_temp[session_id][client_id]["speech_buffer"][self.data_client_temp[session_id][client_id]["last_overlap_start_byte"]:]
                        # self.data_client_temp[session_id][client_id]["last_overlap_start_byte"] = None  # reset ngay sau khi dùng

                    audio_array = np.frombuffer(self.data_client_temp[session_id][client_id]["speech_buffer"][self.data_client_temp[session_id][client_id]["last_overlap_start_byte"]:], dtype=np.int16).astype(np.float32) / 32768.0

                    # Lưu file WAV để debug (optional)
                    filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".wav"
                    filepath = os.path.join("recordings", filename)
                    os.makedirs("recordings", exist_ok=True)
                    with wave.open(filepath, 'wb') as wf:
                        wf.setnchannels(1)
                        wf.setsampwidth(2)
                        wf.setframerate(SAMPLE_RATE)
                        wf.writeframes(self.data_client_temp[session_id][client_id]["speech_buffer"][self.data_client_temp[session_id][client_id]["last_overlap_start_byte"]:])
                    log.debug(f"Saved audio chunk to {filepath}")

                    # Nhận diện
                    time_start = time.time()
                    segments, _ = self.model.transcribe(audio_array, beam_size=1, language=target_language)
                    # Chuyển generator thành list để tái sử dụng
                    temp_segments = list(segments)
                    print(temp_segments)
                    text = " ".join([seg.text.strip() for seg in temp_segments if seg.text.strip()])
                    time_end = time.time()

                    if text:
                        log.info(f"Transcription (client={client_id}, session={session_id}): {text}")
                        log.debug(f"Processing time: {time_end - time_start:.2f}s")
                        print(NEON_GREEN + text)
                        if self.data_client_temp[session_id][client_id]["buffer_count"] == 1 and len(text) > 7:
                            print("nghi ngờ")
                            self.data_client_temp[session_id][client_id]["last_text"] = text
                            continue
                        if self.data_client_temp[session_id][client_id]["buffer_count"] == 2 and len(text) > 30:
                            print("nghi ngờ")
                            self.data_client_temp[session_id][client_id]["last_text"] = text
                            continue
                        elif self.data_client_temp[session_id][client_id]["last_text"]  == text :
                            continue
                        self.data_client_temp[session_id][client_id]["last_text"]  = text
                        self.result_queue.put(("transcripts", {
                            "type": "transcripts",
                            "text": text,
                            "language": target_language, 
                            "session_id": session_id,
                            "client_id": client_id
                        }))

                        total = len(temp_segments)
                        print(f"total = {total}")
                        if total >= OVERLAP_MIN_SEGMENT:
                            print("đã đi vào đây")
                            
                            overlap_start_sec = temp_segments[-OVERLAP_SEGMENT_COUNT].start
                            print("bắt đầu từ s thứ: ",overlap_start_sec)
                            self.data_client_temp[session_id][client_id]["last_overlap_start_byte"] = int(overlap_start_sec * SAMPLE_RATE * 2)
                            log.debug(f"Next overlap will start at byte {self.data_client_temp[session_id][client_id]["last_overlap_start_byte"]}")

                elif self.vad_trigger:
                    log.debug(f"Speech ended for client={client_id}, session={session_id}")
                    self.vad_trigger = False
                    self.data_client_temp[session_id][client_id] = default_client()

                else:
                    self.data_client_temp[session_id][client_id] = default_client()

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
