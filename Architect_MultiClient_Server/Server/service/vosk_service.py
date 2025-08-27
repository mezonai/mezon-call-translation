import json
import os
import time
import queue
import threading
import logging
from vosk import Model, KaldiRecognizer
from utils.vad import SileroVAD
import numpy as np

from dotenv import load_dotenv

log = logging.getLogger(__name__)

# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))


SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 16000))
MIN_TEXT_LENGTH = int(os.getenv("MIN_TEXT_LENGTH", 2))
TRANSLATION_INTERVAL = float(os.getenv("TRANSLATION_INTERVAL", 0.8))
VOSK_MODEL_PATH = os.getenv("VOSK_MODEL_PATH", "vosk-model-small-en-us-0.15")
AUDIO_QUEUE_MAXSIZE = int(os.getenv("AUDIO_QUEUE_MAXSIZE", 100))
RESULT_QUEUE_MAXSIZE = int(os.getenv("RESULT_QUEUE_MAXSIZE", 100))
AUDIO_TASK_QUEUE_MAXSIZE = int(os.getenv("AUDIO_TASK_QUEUE_MAXSIZE", 100))

class STTVoskService:
    def __init__(self, model_path=VOSK_MODEL_PATH):
        log.info("Initializing STTVoskService...")

        if not os.path.exists(model_path):
            raise FileNotFoundError(f"VOSK model not found at {model_path}")
        self.model = Model(model_path)

        self.recognizers = {}
        self.client_state = {}

        self.audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)
        self.result_queue = queue.Queue(maxsize=RESULT_QUEUE_MAXSIZE)
        # self.audio_task_queue = queue.Queue(maxsize=AUDIO_TASK_QUEUE_MAXSIZE)
        self.stop_event = threading.Event()

        #Silero vad
        self.vad = SileroVAD()

        self.thread = threading.Thread(target=self.stt_worker, daemon=True)
        self.thread.start()

    def get_or_create_recognizer(self, client_id, session_id):
        key = (client_id, session_id)
        if key not in self.recognizers:
            log.info(f"Creating recognizer for {key}")
            self.recognizers[key] = KaldiRecognizer(self.model, SAMPLE_RATE)
            self.recognizers[key].SetWords(False)
            self.client_state[key] = {
                "last_translation_time": time.time(),
                "last_queued_text": "",
            }
        return self.recognizers[key], self.client_state[key]

    def stt_worker(self):
        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.1)

                # Convert chunk bytes to float32 normalized audio
                audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0

                # Unpack boolean from tuple returned by VAD
                triggered = self.vad.is_speech(audio_np, sample_rate=SAMPLE_RATE)

                recognizer, state = self.get_or_create_recognizer(client_id, session_id)

                if recognizer.AcceptWaveform(chunk):
                    result = json.loads(recognizer.Result())
                    text = result.get("text", "").strip()
                    if len(text) >= MIN_TEXT_LENGTH and text.lower() != "the":
                        self.result_queue.put(("transcripts", {
                        "type": "transcripts",
                        "text": text,
                        "is_final": True,
                        "session_id": session_id,
                        "client_id": client_id
                        }))
                        self.queue_translation(text, client_id, session_id, state, is_final=True)
                else:
                    partial = json.loads(recognizer.PartialResult())
                    text = partial.get("partial", "").strip()
                    if text and text.lower() != "the" :
                        self.result_queue.put(("transcripts", {
                            "type": "transcripts",
                            "text": text,
                            "is_final": False,
                            "session_id": session_id,
                            "client_id": client_id
                        }))

                        self.queue_translation(text, client_id, session_id, state, is_final=False)

            except queue.Empty:
                continue

    def queue_translation(self, text, client_id, session_id, state, is_final):
        task = {
            "text": text,
            "is_final": is_final,
            "session_id": session_id,
            "client_id": client_id
        }
        # self.audio_task_queue.put(task)
        state["last_queued_text"] = text
        log.info(f"[VOSK-{'FINAL' if is_final else 'PARTIAL'}] Queued for translation from {client_id}: '{text}'")

    def submit_audio(self, chunk, client_id, session_id):
        self.audio_queue.put((chunk, client_id, session_id))

    def get_result_nowait(self):
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def shutdown(self):
        log.info("Shutting down STTVoskService...")
        self.stop_event.set()
        self.thread.join()
        # self.translator_worker.join()
        log.info("Service shut down.")


stt_service_vosk = STTVoskService()
