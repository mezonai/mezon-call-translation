from dataclasses import dataclass
from typing import Tuple, Optional
import os

@dataclass
class VadConfig:
    """Voice Activity Detection configuration"""
    zcr_thresh: Tuple[float, float] = (0.02, 0.2)
    energy_thresh: float = 0.001
    ma_window: int = 8
    analysis_duration_ms: int = 30
    
    @classmethod
    def from_env(cls) -> 'VadConfig':
        """Create config from environment variables"""
        return cls(
            zcr_thresh=(
                float(os.getenv('VAD_ZCR_THRESH_LOW', '0.02')),
                float(os.getenv('VAD_ZCR_THRESH_HIGH', '0.2'))
            ),
            energy_thresh=float(os.getenv('VAD_ENERGY_THRESH', '0.001')),
            ma_window=int(os.getenv('VAD_MA_WINDOW', '8')),
            analysis_duration_ms=int(os.getenv('VAD_ANALYSIS_DURATION_MS', '30'))
        )
    
    def validate(self) -> bool:
        """Validate configuration values"""
        if not (0 <= self.zcr_thresh[0] < self.zcr_thresh[1] <= 1.0):
            return False
        if not (0 <= self.energy_thresh <= 1.0):
            return False
        if self.ma_window < 3:
            return False
        if self.analysis_duration_ms < 10:
            return False
        return True

@dataclass
class AudioProcessorConfig:
    """Audio processor configuration"""
    sample_rate: int = 16000
    channels: int = 1
    chunk_duration_ms: int = 50
    overlap_chunks: int = 2
    enable_playback: bool = False
    min_speech_frames: int = 10
    max_buffer_size: int = 32768  # 32KB
    silent_threshold: int = 50
    vad_config: VadConfig = None

    def __post_init__(self):
        if self.vad_config is None:
            self.vad_config = VadConfig()
    
    @classmethod
    def from_env(cls) -> 'AudioProcessorConfig':
        """Create config from environment variables"""
        return cls(
            sample_rate=int(os.getenv('AUDIO_SAMPLE_RATE', '16000')),
            channels=int(os.getenv('AUDIO_CHANNELS', '1')),
            chunk_duration_ms=int(os.getenv('AUDIO_CHUNK_DURATION_MS', '10')),
            overlap_chunks=int(os.getenv('AUDIO_OVERLAP_CHUNKS', '2')),
            enable_playback=bool(int(os.getenv('AUDIO_ENABLE_PLAYBACK', '0'))),
            min_speech_frames=int(os.getenv('AUDIO_MIN_SPEECH_FRAMES', '10')),
            max_buffer_size=int(os.getenv('AUDIO_MAX_BUFFER_SIZE', '32768')),
            silent_threshold=int(os.getenv('AUDIO_SILENT_THRESHOLD', '50')),
            vad_config=VadConfig.from_env()
        )
    
    def validate(self) -> bool:
        """Validate configuration values"""
        if self.sample_rate not in [8000, 16000, 32000, 44100, 48000]:
            return False
        if self.channels not in [1, 2]:
            return False
        if not (5 <= self.chunk_duration_ms <= 100):
            return False
        if not (0 <= self.overlap_chunks <= 5):
            return False
        if not (1 <= self.min_speech_frames <= 100):
            return False
        if not (1024 <= self.max_buffer_size <= 1048576):  # 1KB to 1MB
            return False
        if not (10 <= self.silent_threshold <= 200):
            return False
        return self.vad_config.validate()

@dataclass
class AudioThreadingConfig:
    """Audio threading configuration"""
    processing_threads: int = 2
    max_queue_size: int = 1000
    thread_pool_timeout: float = 5.0
    
    @classmethod
    def from_env(cls) -> 'AudioThreadingConfig':
        """Create config from environment variables"""
        return cls(
            processing_threads=int(os.getenv('AUDIO_PROCESSING_THREADS', '2')),
            max_queue_size=int(os.getenv('AUDIO_MAX_QUEUE_SIZE', '1000')),
            thread_pool_timeout=float(os.getenv('AUDIO_THREAD_POOL_TIMEOUT', '5.0'))
        )
    
    def validate(self) -> bool:
        """Validate configuration values"""
        if not (1 <= self.processing_threads <= 8):
            return False
        if not (100 <= self.max_queue_size <= 10000):
            return False
        if not (1.0 <= self.thread_pool_timeout <= 30.0):
            return False
        return True

class AudioConfigError(Exception):
    """Raised when audio configuration is invalid"""
    pass
