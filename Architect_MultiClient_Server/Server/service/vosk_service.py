import threading
import queue
import numpy as np
from vosk import Model, KaldiRecognizer
import json


SAMPLE_RATE = 16000
CHUNK_DURATION = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  

class STTVoskService:
    def __init__(self):
        
        self.model = Model("path/to/vosk-model")  # Thay đổi path
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.stt_worker, daemon=True)
        self.thread.start()

    def stt_worker(self):
        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.5)
                
                # Xử lý audio chunk với Vosk
                audio_data = np.frombuffer(chunk, dtype=np.int16)
                
                # Tạo recognizer cho chunk này
                recognizer = KaldiRecognizer(self.model, SAMPLE_RATE)
                recognizer.AcceptWaveform(audio_data.tobytes())
                
                # Lấy kết quả
                result = json.loads(recognizer.FinalResult())
                text = result.get("text", "").strip()
                
                if text:
                    self.result_queue.put((text, client_id, session_id))
            except queue.Empty:
                continue

    def submit_audio(self, chunk, client_id, session_id):
        # Kiểm tra chunk size và format
        if len(chunk) >= CHUNK_SIZE:
            self.audio_queue.put((chunk, client_id, session_id))
        else:
            # Có thể xử lý chunk nhỏ hơn hoặc bỏ qua
            pass

    def get_result_nowait(self):
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self):
        self.stop_event.set()
        self.thread.join()

stt_service_vosk = STTVoskService() 