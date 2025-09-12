import os
from dotenv import load_dotenv

load_dotenv()

class VADConfig:
    # Load từ environment variables, sử dụng giá trị mặc định nếu không có
    ZCR_THRESH_LOW = float(os.getenv('VAD_ZCR_THRESH_LOW', '0.05'))
    ZCR_THRESH_HIGH = float(os.getenv('VAD_ZCR_THRESH_HIGH', '0.3'))
    ENERGY_THRESH = float(os.getenv('VAD_ENERGY_THRESH', '0.005'))
    MA_WINDOW = int(os.getenv('VAD_MA_WINDOW', '16'))
    PRE_SPEECH_BUFFER_MS = int(os.getenv('VAD_PRE_SPEECH_BUFFER_MS', '200'))
    MIN_SPEECH_FRAMES = int(os.getenv('VAD_MIN_SPEECH_FRAMES', '15'))
    SILENT_THRESHOLD_MS = int(os.getenv('VAD_SILENT_THRESHOLD_MS', '500'))
    
    @classmethod
    def get_config(cls):
        return {
            'zcr_low_threshold': cls.ZCR_THRESH_LOW,
            'zcr_high_threshold': cls.ZCR_THRESH_HIGH,
            'energy_thresh': cls.ENERGY_THRESH,
            'ma_window': cls.MA_WINDOW,
            'pre_speech_buffer_frames': cls.PRE_SPEECH_BUFFER_MS // 10,  # Convert ms to frames (10ms per frame)
            'min_speech_frames': cls.MIN_SPEECH_FRAMES,
            'silent_threshold': cls.SILENT_THRESHOLD_MS // 10  # Convert ms to frames (10ms per frame)
        }
