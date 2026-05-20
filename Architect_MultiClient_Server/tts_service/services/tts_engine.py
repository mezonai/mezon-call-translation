import os
import asyncio
import hashlib
import re
from enum import Enum
from functools import lru_cache
from concurrent.futures import ThreadPoolExecutor
from typing import Optional
import numpy as np
from pathlib import Path
from cachetools import LFUCache
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

        if config is None:
            config = TTSConfig()
        
        # LFU Cache: automatically evicts least-frequently-used items when reaching maxsize
        self.cache = LFUCache(maxsize=config.lfu_cache_maxsize)

        logger.info(f"TTSEngine initialized (sample_rate={sample_rate}Hz, model=Kokoro-82M, LFU cache with maxsize={config.lfu_cache_maxsize})")

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

            # CACHE CHECK - LFUCache automatically tracks access frequency
            cache_key = self._make_cache_key(text, selected_voice, speed)
            if cache_key in self.cache:
                logger.info("TTS cache HIT")
                return self.cache[cache_key]

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

            duration = len(audio_np) / self.sample_rate
            logger.info(f"Synthesized {duration:.2f}s audio")

            # SAVE TO CACHE - LFUCache automatically evicts least-frequently-used items when full
            self.cache[cache_key] = audio_np

            return audio_np

        except Exception as e:
            logger.error(f"Synthesis failed: {e}", exc_info=True)
            raise
    def clear_cache(self):
        self.cache.clear()
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
    """Get the TTS engine singleton instance"""
    global tts_engine
    if tts_engine is None:
        config = TTSConfig()
        tts_engine = TTSEngine(model_path=config.model_path)
    return tts_engine