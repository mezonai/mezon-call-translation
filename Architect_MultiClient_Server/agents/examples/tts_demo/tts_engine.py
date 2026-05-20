import os
import asyncio
import time
import hashlib
import re
from enum import Enum
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
from pathlib import Path
from kokoro import KPipeline

from ..logger import get_logger
from ..config.app_config import TTSConfig

logger = get_logger(__name__)


class KokoroVoice(str, Enum):
    AF_HEART = "af_heart"
    AF_BELLA = "af_bella"
    AF_SARAH = "af_sarah"
    AM_ADAM = "am_adam"
    AM_MICHAEL = "am_michael"
    BF_EMMA = "bf_emma"
    BF_ISABELLA = "bf_isabella"
    BM_GEORGE = "bm_george"
    BM_LEWIS = "bm_lewis"




class TTSEngine:
    def __init__(self, sample_rate: int = 24000, model_path: str = "models/kokoro_models", config: TTSConfig = None):
        self.sample_rate = sample_rate
        self.model_dir = Path(model_path)
        self.pipeline = None
        self.lang_code = 'a'

        self.cache = {}
        self.cache_size = 0
        if config is None:
            config = TTSConfig()
        self.max_cache_size = config.max_cache_size

        logger.info(f"TTSEngine initialized (sample_rate={sample_rate}Hz, model=Kokoro-82M, max_cache_size={self.max_cache_size})")

    async def load(self) -> bool:
        try:
            logger.info("Loading Kokoro TTS model...")

            if not self.model_dir.exists():
                logger.warning(f"Model directory not found: {self.model_dir}")
                return False

            loop = asyncio.get_event_loop()
            with ThreadPoolExecutor() as pool:
                self.pipeline = await loop.run_in_executor(pool, self._load_model_sync)

            if self.pipeline is None:
                return False

            logger.info("✅ Kokoro TTS model loaded successfully")
            return True

        except Exception as e:
            logger.error(f"Failed to load TTS model: {e}", exc_info=True)
            return False

    def _load_model_sync(self):
        try:
            os.environ['HF_HOME'] = str(self.model_dir.parent)
            pipeline = KPipeline(lang_code=self.lang_code, repo_id='hexgrad/Kokoro-82M')
            return pipeline
        except Exception as e:
            logger.error(f"Error in _load_model_sync: {e}", exc_info=True)
            return None

    # =========================
    # CACHE UTILS
    # =========================

    def _normalize_text(self, text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r'\s+', ' ', text)
        return text

    def _make_cache_key(self, text: str, voice: str, speed: float) -> str:
        normalized = self._normalize_text(text)
        raw = f"{normalized}|{voice}|{speed}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _get_from_cache(self, key: str):
        item = self.cache.get(key)
        if item:
            item["last_access"] = time.time()
            logger.info("TTS cache HIT")
            return item["audio"]
        return None

    def _add_to_cache(self, key: str, audio: np.ndarray):
        size = audio.nbytes

        if size > self.max_cache_size:
            return

        while self.cache_size + size > self.max_cache_size:
            self._evict_lru()

        self.cache[key] = {
            "audio": audio,
            "size": size,
            "last_access": time.time()
        }
        self.cache_size += size

        logger.debug(f"Cache add: size={size/1024:.2f}KB total={self.cache_size/1024/1024:.2f}MB")

    def _evict_lru(self):
        if not self.cache:
            return

        oldest_key = min(self.cache, key=lambda k: self.cache[k]["last_access"])

        evicted_size = self.cache[oldest_key]["size"]
        del self.cache[oldest_key]
        self.cache_size -= evicted_size

        logger.debug(f"Evicted cache: {evicted_size/1024:.2f}KB")

    # =========================
    # MAIN SYNTHESIS
    # =========================

    def synthesize(
        self,
        text: str,
        voice: KokoroVoice = KokoroVoice.AF_HEART,
        speed: float = 1.0
    ) -> np.ndarray:

        if self.pipeline is None:
            raise RuntimeError("TTS model not loaded. Call load() first.")

        try:
            selected_voice = voice.value if isinstance(voice, KokoroVoice) else voice

            # CACHE CHECK
            cache_key = self._make_cache_key(text, selected_voice, speed)
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                return cached

            logger.info(f"Synthesizing: '{text[:50]}...' (voice={selected_voice}, speed={speed}x)")

            generator = self.pipeline(
                text,
                voice=selected_voice,
                speed=speed,
                split_pattern=r'\n+'
            )

            all_audio = []
            for _, _, audio in generator:
                all_audio.append(audio)

            if not all_audio:
                return np.array([], dtype=np.float32)

            audio_np = np.concatenate(all_audio)

            # OPTIMIZE MEMORY
            audio_np = audio_np.astype(np.float16)

            duration = len(audio_np) / self.sample_rate
            logger.info(f"Synthesized {duration:.2f}s audio")

            # SAVE CACHE
            self._add_to_cache(cache_key, audio_np)

            return audio_np

        except Exception as e:
            logger.error(f"Synthesis failed: {e}", exc_info=True)
            raise
    def clear_cache(self):
        self.cache.clear()
        self.cache_size = 0
        logger.info("TTS cache cleared")
        
    def cleanup(self):
        try:
            # cleanup model
            if self.pipeline is not None:
                del self.pipeline
                self.pipeline = None

            #cleanup cache
            self.clear_cache()

            logger.info("TTS engine resources released (model + cache)")

        except Exception as e:
            logger.error(f"Error during cleanup: {e}", exc_info=True)

    def __del__(self):
        self.cleanup()


tts_engine: Optional[TTSEngine] = None


@lru_cache(maxsize=1)
def get_tts_engine() -> TTSEngine:
    global tts_engine
    if tts_engine is None:
        model_path = os.getenv('TTS_MODEL_PATH', 'models/kokoro_models')
        config = TTSConfig()
        tts_engine = TTSEngine(model_path=model_path, config=config)
    return tts_engine