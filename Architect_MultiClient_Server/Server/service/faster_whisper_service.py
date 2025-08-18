import json
import os
import time
import queue
import threading
import logging
import sys
import numpy as np
import wave
from datetime import datetime

from faster_whisper import WhisperModel
from utils.vad import SileroVAD
from service.nllb_service import TranslatorWorker, TranslatorConfig
from session_manager import session_manager

from dotenv import load_dotenv

# ===== Logger Setup =====
class LogFormatter(logging.Formatter):
    COLORS = {
        "DEBUG": "\033[94m",    # Blue
        "INFO": "\033[92m",     # Green
        "WARNING": "\033[93m",  # Yellow
        "ERROR": "\033[91m",    # Red
        "CRITICAL": "\033[95m"  # Magenta
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

# Load environment variables from .env file
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

# Configuration from environment variables
SAMPLE_RATE = int(os.getenv("SAMPLE_RATE", 16000))
CHUNK_DURATION = 1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_DURATION) * 2  # 16-bit PCM
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cuda")
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "float16")
MIN_TEXT_LENGTH = int(os.getenv("MIN_TEXT_LENGTH", 2))
TRANSLATION_INTERVAL = float(os.getenv("TRANSLATION_INTERVAL", 0.8))
AUDIO_QUEUE_MAXSIZE = int(os.getenv("AUDIO_QUEUE_MAXSIZE", 100))
RESULT_QUEUE_MAXSIZE = int(os.getenv("RESULT_QUEUE_MAXSIZE", 100))
AUDIO_TASK_QUEUE_MAXSIZE = int(os.getenv("AUDIO_TASK_QUEUE_MAXSIZE", 100))

class STTFasterWhisperService:
    def __init__(self):
        """Initializes the Faster Whisper STT service with integrated translation."""
        log.info(f"Initializing Whisper model ({WHISPER_MODEL}, {WHISPER_COMPUTE_TYPE}, {WHISPER_DEVICE})...")
        self.model = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
        
        # Queues for inter-thread communication
        self.audio_queue = queue.Queue(maxsize=AUDIO_QUEUE_MAXSIZE)
        self.result_queue = queue.Queue(maxsize=RESULT_QUEUE_MAXSIZE)
        self.audio_task_queue = queue.Queue(maxsize=AUDIO_TASK_QUEUE_MAXSIZE)
        self.stop_event = threading.Event()

        # Dictionaries to manage state per client/session
        self.client_state = {}

        # Initialize the VAD service
        log.info("Initializing SileroVAD...")
        self.vad = SileroVAD()
        self.vad_trigger = False
        self.speech_buffer = bytes()
        self.buffer_count = 0

        # Initialize and start the translation worker
        # self.translator_worker = TranslatorWorker(
        #     TranslatorConfig(
        #         NLLB_MODEL_NAME=os.getenv("NLLB_MODEL_NAME", "facebook/nllb-200-distilled-600M"),
        #         SOURCE_LANG=os.getenv("NLLB_SOURCE_LANG", "eng_Latn"),
        #         TARGET_LANG=os.getenv("NLLB_TARGET_LANG", "vie_Latn"),
        #     ),
        #     self.audio_task_queue,
        #     self.result_queue,
        #     self.stop_event
        # )
        # self.translator_worker.start()
        # log.info("Translator worker thread started.")

        # Start the STT worker thread
        self.thread = threading.Thread(target=self.stt_worker, daemon=True, name="STT-Worker")
        self.thread.start()
        log.info("STT worker thread started.")

    def get_or_create_client_state(self, client_id, session_id):
        """Gets or creates the state dictionary for a given client/session."""
        key = (client_id, session_id)
        if key not in self.client_state:
            log.info(f"Creating client state for {key}")
            self.client_state[key] = {
                "speech_buffer": bytes(),
                "last_transcription_time": time.time(),
                "last_queued_text": "",
            }

        return self.client_state[key]

    def stt_worker(self):
        while not self.stop_event.is_set():
            try:
                chunk, client_id, session_id = self.audio_queue.get(timeout=0.1)
                log.debug(f"Received chunk from client={client_id}, session={session_id}, size={len(chunk)} bytes")

                # Get language from SessionManager
                client_lang = session_manager.get_client_language(session_id, client_id)
                print("ngon ngu: ", client_lang)
                if client_lang:
                    target_language = client_lang
                else:
                    target_language = "en"


                log.debug(f"Processing chunk for client={client_id}, lang={target_language}")

                state = self.get_or_create_client_state(client_id, session_id)
                # Convert chunk bytes to float32 normalized audio
                audio_np = np.frombuffer(chunk, dtype=np.int16).astype(np.float32) / 32768.0
                
                # Check for speech activity
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
                    # filename = datetime.now().strftime("%Y%m%d_%H%M%S") + ".wav"
                    # filepath = os.path.join("recordings", filename)
                    # os.makedirs("recordings", exist_ok=True)  
                    # with wave.open(filepath, 'wb') as wf:
                    #     wf.setnchannels(1)  # mono
                    #     wf.setsampwidth(2)  # 16-bit PCM = 2 bytes
                    #     wf.setframerate(SAMPLE_RATE)
                    #     wf.writeframes(self.speech_buffer)
                    # log.debug(f"Saved audio chunk to {filepath}")

                    # Nhận diện
                    time_start = time.time()
                    segments, _ = self.model.transcribe(audio_array, beam_size=1, language=target_language,task="transcribe")
                    text = " ".join([seg.text.strip() for seg in segments if seg.text.strip()])
                    time_end = time.time()

                    if text:
                        log.info(f"Transcription (client={client_id}, session={session_id}): {text}")
                        log.debug(f"Processing time: {time_end - time_start:.2f}s")

                        self.result_queue.put(("transcripts", {
                            "type": "transcripts",
                            "text": text,
                            "is_final": True,  # Since this is after VAD speech end
                            "session_id": session_id,
                            "client_id": client_id
                        }))

                        # Also queue for translation
                        state["last_queued_text"] = text
                        # self.queue_translation(text, client_id, session_id, state, is_final=True)


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
            except Exception as e:
                log.error(f"Error in STT worker: {e}", exc_info=True)

    def queue_translation(self, text, client_id, session_id, state, is_final):
        """Queues a translation task for the NLLB service."""
        task = {
            "text": text,
            "is_final": is_final,
            "session_id": session_id,
            "client_id": client_id
        }
        self.audio_task_queue.put(task)
        state["last_queued_text"] = text
        log.info(f"[WHISPER-{'FINAL' if is_final else 'PARTIAL'}] Queued for translation from {client_id}: '{text}'")

    def submit_audio(self, chunk, client_id, session_id,):
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
        self.translator_worker.join()
        log.info("Service shut down.")

stt_service = STTFasterWhisperService()