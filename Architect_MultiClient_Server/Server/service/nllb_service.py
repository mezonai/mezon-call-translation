import queue
import threading
import time
import torch
import logging
import os
from dataclasses import dataclass
from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

from dotenv import load_dotenv

log = logging.getLogger(__name__)
 
# Load environment variables
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

@dataclass
class TranslatorConfig:
    NLLB_MODEL_NAME: str = os.getenv("NLLB_MODEL_NAME", "models/translate/nllb-200")
    SOURCE_LANG: str = os.getenv("NLLB_SOURCE_LANG", "en_Latn")
    TARGET_LANG: str = os.getenv("NLLB_TARGET_LANG", "vie_Latn")

class TranslatorWorker(threading.Thread):
    """
    Thread dịch văn bản sử dụng NLLB.
    Nhận task từ task_queue và trả kết quả về result_queue.
    """
    def __init__(self, config: TranslatorConfig, task_queue: queue.Queue, result_queue: queue.Queue, stop_event: threading.Event):
        super().__init__(daemon=True)
        self.config = config
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.stop_event = stop_event
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.tokenizer = None
        self.translator = None
        self._load_model()

    def _load_model(self):
        log.info(f"[TranslatorWorker] Using device: {self.device}")
        self.tokenizer = AutoTokenizer.from_pretrained(self.config.NLLB_MODEL_NAME)
        self.translator = AutoModelForSeq2SeqLM.from_pretrained(
            self.config.NLLB_MODEL_NAME, device_map="auto"
        )
        log.info("[TranslatorWorker] NLLB model loaded.") 

    def run(self):
        log.info("[TranslatorWorker] Worker thread started.")
        while not self.stop_event.is_set():
            try:
                task = self.task_queue.get(timeout=1)
                self._process_and_queue_task(task)
                self.task_queue.task_done()
            except queue.Empty:
                continue
        log.info("[TranslatorWorker] Worker thread stopped.")

    def _process_and_queue_task(self, task):
        text_to_translate = task["text"]
        session_id = task["session_id"]
        client_id = task["client_id"]
        is_final = task["is_final"]

        with torch.no_grad():
            self.tokenizer.src_lang = self.config.SOURCE_LANG
            encoded = self.tokenizer(text_to_translate, return_tensors="pt").to(self.device)
            generated_tokens = self.translator.generate(
                **encoded,
                forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(self.config.TARGET_LANG),
                max_length=int(len(text_to_translate.split()) * 2.5) + 10
            )
            translation = self.tokenizer.batch_decode(generated_tokens, skip_special_tokens=True)[0]

        response_payload = {
            "type": "translation",
            "original": text_to_translate,
            "translation": translation,
            "is_final": is_final,
            "client_id": client_id,
        }
        
        log.info(f"Translated for {client_id}, queuing for dispatch: '{translation}'")
        self.result_queue.put((response_payload, client_id, session_id))
