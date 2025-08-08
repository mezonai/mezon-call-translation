import threading
import queue
import numpy as np
from faster_whisper import WhisperModel

# Audio configuration
SAMPLE_RATE = 16000
CHUNK_DURATION = 2
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # 16-bit PCM
OVERLAP_SIZE = int(CHUNK_SIZE * 0.25)  # 10% overlap

class STTFasterWhisperService:
    def __init__(self):
        self.model = WhisperModel("base", device="cpu", compute_type="int8")
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self.stt_worker, daemon=True)
        self.thread.start()

    def stt_worker(self):
        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.1)
                
                # Xử lý audio chunk đúng format
                audio_data = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                
                # STT processing với Faster Whisper
                segments, _ = self.model.transcribe(audio_data, language="en", beam_size=1)
                text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])
                
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

stt_service = STTFasterWhisperService()