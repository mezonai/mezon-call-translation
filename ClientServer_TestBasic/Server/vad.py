import torch
import numpy as np

class VoiceActivityDetector:
    def __init__(self, threshold=0.5):
        self.model, self.utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', trust_repo=True)
        (self.get_speech_ts,
         self.save_audio,
         self.read_audio,
         self.VADIterator,
         self.collect_chunks) = self.utils
        self.threshold = threshold

    def is_speech(self, audio, sample_rate=16000):
        # input must be torch tensor
        audio_tensor = torch.from_numpy(audio).unsqueeze(0)

        # get speech timestamps (list of dicts)
        speech_ts = self.get_speech_ts(audio_tensor, self.model, sampling_rate=sample_rate)

        # return True if speech found
        return len(speech_ts) > 0
