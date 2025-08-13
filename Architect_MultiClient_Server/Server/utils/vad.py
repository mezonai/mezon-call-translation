import torch
import numpy as np

class SileroVAD:
    def __init__(self, threshold=0.5, device=None):
        self.model, self.utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            trust_repo=True
        )
        (self.get_speech_ts,
         self.save_audio,
         self.read_audio,
         self.VADIterator,
         self.collect_chunks) = self.utils

        self.threshold = threshold
        self.device = device or ('cuda' if torch.cuda.is_available() else 'cpu')
        self.model.to(self.device)

    def is_speech(self, audio: np.ndarray, sample_rate=16000) -> bool:
        """
        Check if given audio contains speech.

        Args:
            audio: 1D numpy float32 array normalized between -1 and 1
            sample_rate: audio sample rate (must be 16000 for Silero VAD)

        Returns:
            True if speech detected, False otherwise
        """
        # Make sure audio is float32 numpy array
        if not isinstance(audio, np.ndarray):
            raise ValueError("Audio must be a numpy array")
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        # Convert to Torch tensor on correct device
        audio_tensor = torch.from_numpy(audio).to(self.device)
        if audio_tensor.dim() == 1:
            audio_tensor = audio_tensor.unsqueeze(0)  # batch dimension

        # Get speech timestamps (list of dicts with start/end)
        speech_timestamps = self.get_speech_ts(audio_tensor, self.model, sampling_rate=sample_rate)

        # If any speech segments detected, return True
        return len(speech_timestamps) > 0
