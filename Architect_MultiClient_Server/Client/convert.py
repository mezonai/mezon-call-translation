import soundfile as sf
import librosa

mp3_path = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Client\testAudio.mp3"
wav_path = r"E:\NCC\mezon-call-translation\Architect_MultiClient_Server\Client\testAudio.wav"

# Load file mp3 (librosa sẽ decode)
y, sr = librosa.load(mp3_path, sr=None)  # giữ nguyên sample rate gốc

# Ghi ra wav
sf.write(wav_path, y, sr)

print("Đã convert xong:", wav_path)
