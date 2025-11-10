import numpy as np
import librosa
from scipy.io import wavfile
import time
import threading
from collections import deque
from queue import Queue
import pyaudio
from src.logger import get_logger
from src.config import get_config

# ==============================
# Setup logging
# ==============================
logger = get_logger(__name__)
# ==============================
# ZCR Filter với Moving Average (optimized for 30ms chunks)
# ==============================
class ZCRFilter:
    def __init__(self, zcr_thresh=(0.05, 0.15), energy_thresh=0.001, ma_window=5, analysis_duration_ms=30):
        self.zcr_thresh = zcr_thresh
        self.energy_thresh = energy_thresh
        self.analysis_duration_ms = analysis_duration_ms
        # moving average window
        self.ma_window = max(ma_window, 3)
        self.history = []
        
        logger.info(
            f"🎛️ ZCR Filter: zcr_thresh={self.zcr_thresh}, "
            f"energy_thresh={self.energy_thresh}, "
            f"MA window={self.ma_window}, analysis={analysis_duration_ms}ms"
        )

    def check(self, chunk: np.ndarray) -> bool:
        if len(chunk) == 0:
            return False

        # --- ZCR ---
        frame_length = min(len(chunk), 1024)
        zcr = np.mean(librosa.feature.zero_crossing_rate(chunk, frame_length=frame_length)[0])
        
        # --- Energy (RMS) ---
        energy = np.mean(chunk ** 2)

        # --- moving average ZCR ---
        self.history.append(zcr)
        if len(self.history) > self.ma_window:
            self.history.pop(0)
        zcr_ma = np.mean(self.history)

        # --- quyết định speech ---
        is_speech = (
            self.zcr_thresh[0] <= zcr_ma <= self.zcr_thresh[1]
            and energy > self.energy_thresh
        )

        logger.debug(f"    ZCR: {zcr:.4f}, MA: {zcr_ma:.4f}, Energy: {energy:.6f}, Speech: {is_speech}")

        return is_speech
    
    def _calculate_confidence(self, zcr, zcr_ma):
        """Tính confidence score cho decision"""
        if len(self.history) < 2:
            return 0.5
            
        # Tính stability của ZCR (ít biến động = confidence cao)
        zcr_std = np.std(self.history)
        max_std = 0.1  # Empirical threshold
        stability = max(0, 1 - (zcr_std / max_std))
        
        # Tính distance từ threshold boundaries
        lower, upper = self.zcr_thresh
        if lower <= zcr_ma <= upper:
            # Speech: confidence cao khi ở giữa range
            mid_point = (lower + upper) / 2
            distance_from_center = abs(zcr_ma - mid_point) / (upper - lower) * 2
            boundary_confidence = 1 - distance_from_center
        else:
            # Non-speech: confidence cao khi xa boundaries
            if zcr_ma < lower:
                boundary_confidence = min(1.0, (lower - zcr_ma) / lower)
            else:
                boundary_confidence = min(1.0, (zcr_ma - upper) / upper)
        
        return (stability + boundary_confidence) / 2

# ==============================
# Real-time Audio Player
# ==============================
class RealTimeAudioPlayer:
    def __init__(self, sr=16000, chunk_duration_ms=10):
        self.sr = sr
        self.chunk_duration_ms = chunk_duration_ms
        self.chunk_size = int(chunk_duration_ms * sr / 1000)
        
        # PyAudio setup
        self.audio = pyaudio.PyAudio()
        self.stream = None
        
        # Audio playback queue
        self.playback_queue = Queue()
        self.is_playing = False
        self.playback_thread = None
        
        logger.info(f"🔊 Audio Player initialized: {sr}Hz, {chunk_duration_ms}ms chunks")

    def start_playback(self):
        """Bắt đầu phát audio"""
        if self.is_playing:
            return
            
        try:
            # Mở stream để phát audio
            self.stream = self.audio.open(
                format=pyaudio.paFloat32,
                channels=1,
                rate=self.sr,
                output=True,
                frames_per_buffer=self.chunk_size
            )
            
            self.is_playing = True
            self.playback_thread = threading.Thread(target=self._playback_loop, daemon=True)
            self.playback_thread.start()
            logger.info("🎵 Started audio playback")
            
        except Exception as e:
            logger.error(f"Error starting playback: {e}")

    def stop_playback(self):
        """Dừng phát audio"""
        self.is_playing = False
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.playback_thread:
            self.playback_thread.join(timeout=1.0)
        logger.info("⏹️ Stopped audio playback")

    def add_audio_chunk(self, chunk):
        """Thêm audio chunk vào queue để phát"""
        if self.is_playing:
            self.playback_queue.put(chunk.astype(np.float32))

    def _playback_loop(self):
        """Vòng lặp phát audio từ queue"""
        while self.is_playing:
            try:
                if not self.playback_queue.empty():
                    chunk = self.playback_queue.get(timeout=0.1)
                    if self.stream and len(chunk) > 0:
                        # Phát chunk audio
                        self.stream.write(chunk.tobytes())
                else:
                    time.sleep(0.001)  # Sleep ngắn nếu queue rỗng
            except Exception as e:
                logger.error(f"Error in playback loop: {e}")
                time.sleep(0.001)

    def __del__(self):
        """Cleanup khi object bị destroy"""
        self.stop_playback()
        if hasattr(self, 'audio'):
            self.audio.terminate()

# ==============================
# Real-time Audio Stream Processor với 10ms chunks và 20ms overlap
# ==============================
class RealTimeVADProcessor:
    def __init__(self, sr=16000, chunk_duration_ms=10, overlap_chunks=2, 
                 enable_playback=True, min_speech_frames=None, save_chunks=True, enable_vad=True):
        # Lấy config
        config = get_config()
        
        # Lấy min_speech_frames từ config nếu không được chỉ định
        if min_speech_frames is None:
            min_speech_frames = config.vad.min_speech_frames
        
        self.sr = sr
        self.chunk_duration_ms = chunk_duration_ms
        self.chunk_size = int(chunk_duration_ms * sr / 1000)  # 10ms chunks từ stream
        self.overlap_chunks = overlap_chunks  # Số chunks trước để overlap (2 = 20ms)
        self.enable_playback = enable_playback
        self.min_speech_frames = min_speech_frames  # Số frame tối thiểu để lưu vào buffer
        self.save_chunks = save_chunks  # Flag để kiểm soát việc lưu chunks
        self.enable_vad = enable_vad  # New flag to enable/disable VAD
        
        # Analysis chunk size = current chunk + overlap chunks
        self.overlap_ms = overlap_chunks * chunk_duration_ms
        self.analysis_duration_ms = chunk_duration_ms + self.overlap_ms  # 10ms + 20ms = 30ms
        self.analysis_chunk_size = int(self.analysis_duration_ms * sr / 1000)  # 30ms
        
        # Buffer để lưu các chunks trước đó cho overlap
        self.overlap_buffer = deque(maxlen=overlap_chunks)  # Lưu 2 chunks trước
        
        logger.debug(f"🔄 Overlap config: {overlap_chunks} chunks ({self.overlap_ms}ms) + current chunk ({chunk_duration_ms}ms) = {self.analysis_duration_ms}ms analysis")
        
        # Khởi tạo ZCRFilter với cấu hình từ Config
        self.zcr_filter = ZCRFilter(
            zcr_thresh=config.vad.zcr_thresh,
            ma_window=config.vad.ma_window,
            analysis_duration_ms=self.analysis_duration_ms,
            energy_thresh=config.vad.energy_thresh
        )
        
        # Audio playback
        if self.enable_playback:
            self.audio_player = RealTimeAudioPlayer(sr=sr, chunk_duration_ms=chunk_duration_ms)
        else:
            self.audio_player = None
        
        # Audio processing state
        self.speech_chunks = []
        self.total_chunks = 0
        self.speech_count = 0
        self.silent_streak = 0
        self.silent_threshold = config.vad.silent_threshold_frames  # Số frame im lặng tối đa
        
        # Buffer để lưu trữ các chunk gần đây
        self.pre_speech_buffer_size = config.vad.pre_speech_buffer_frames
        self.chunk_buffer = deque(maxlen=self.pre_speech_buffer_size)
        
        # Buffer tạm thời cho speech candidate (chưa đủ min frames)
        self.temp_speech_buffer = []
        self.consecutive_speech_frames = 0
        
        # Trạng thái speech segment
        self.in_speech_segment = False
        
        # Queue để lưu audio chunks từ stream
        self.audio_queue = Queue()
        self.is_processing = False
        
        # Accumulated audio data for final output
        self.accumulated_speech = []
        
        # ===== THUỘC TÍNH MỚI: Buffer lưu tất cả chunks đã được xử lý =====
        self.processed_chunks_buffer = deque()  # Lưu tất cả chunks đã xử lý (không giới hạn kích thước)
        
        logger.info(f"📊 Min speech frames required: {min_speech_frames} ({min_speech_frames * chunk_duration_ms}ms)")
        logger.info(f"📦 Processed chunks buffer initialized for batching")
        logger.info(f"VAD Enabled: {self.enable_vad}")

    def add_audio_chunk(self, audio_chunk):
        """Thêm audio chunk vào queue để xử lý"""
        if isinstance(audio_chunk, (list, tuple)):
            audio_chunk = np.array(audio_chunk, dtype=np.float32)
        elif not isinstance(audio_chunk, np.ndarray):
            audio_chunk = np.array([audio_chunk], dtype=np.float32)
        
        # Phát audio ngay lập tức
        if self.enable_playback and self.audio_player:
            self.audio_player.add_audio_chunk(audio_chunk)
        # Tạo analysis chunk bằng cách ghép overlap buffer với chunk hiện tại
        analysis_chunks = []
        
        # Thêm các chunks từ overlap buffer
        for overlap_chunk in self.overlap_buffer:
            analysis_chunks.append(overlap_chunk)
        
        # Thêm chunk hiện tại
        analysis_chunks.append(audio_chunk)
        
        # Ghép thành analysis chunk
        if analysis_chunks:
            analysis_chunk = np.concatenate(analysis_chunks)
            
            # Cắt hoặc pad để đảm bảo đúng size
            if len(analysis_chunk) > self.analysis_chunk_size:
                analysis_chunk = analysis_chunk[:self.analysis_chunk_size]
            elif len(analysis_chunk) < self.analysis_chunk_size:
                analysis_chunk = np.pad(analysis_chunk, (0, self.analysis_chunk_size - len(analysis_chunk)), 'constant')
        else:
            # Trường hợp đặc biệt: chưa có overlap buffer
            analysis_chunk = np.pad(audio_chunk, (0, self.analysis_chunk_size - len(audio_chunk)), 'constant')
        
        # Cập nhật overlap buffer với chunk hiện tại
        self.overlap_buffer.append(audio_chunk.copy())
        
        # Đưa vào queue
        self.audio_queue.put({
            'original': audio_chunk,  # Chunk gốc 10ms để lưu
            'analysis': analysis_chunk  # Chunk 30ms để phân tích
        })

    def process_chunk(self, chunk_data):
        """Xử lý một audio chunk"""
        original_chunk = chunk_data['original']  # 10ms chunk
        analysis_chunk = chunk_data['analysis']  # 30ms chunk
        
        self.total_chunks += 1

        # Nếu VAD bị tắt, xử lý tất cả các chunk như là speech
        if not self.enable_vad:
            is_speech = True
        else:
            # Kiểm tra xem chunk có phải là speech không (dùng analysis chunk 30ms)
            is_speech = self.zcr_filter.check(analysis_chunk)

        # Thêm chunk hiện tại vào buffer (lưu original chunk 10ms)
        chunk_data_item = {
            'audio': original_chunk.copy(),
            'is_speech': is_speech,
            'timestamp': time.time(),
            'chunk_id': self.total_chunks
        }
        self.chunk_buffer.append(chunk_data_item)
        
        # ===== THÊM VÀO PROCESSED BUFFER =====
        # Chỉ lưu chunk khi đã trong speech segment hoặc silence chưa vượt ngưỡng
        if self.in_speech_segment:
            # Nếu đang trong speech segment và silence chưa vượt ngưỡng
            if not is_speech and self.silent_streak > self.silent_threshold:
                # Không lưu vì đã vượt ngưỡng im lặng
                pass
            else:
                # Lưu chunk vào processed buffer để phục vụ batching
                self.processed_chunks_buffer.append(original_chunk.copy())

        if is_speech:
            self.consecutive_speech_frames += 1
            self.silent_streak = 0  # reset streak khi có tiếng nói
            
            # Thêm vào temp buffer
            self.temp_speech_buffer.append(original_chunk.copy())
            
            # Kiểm tra nếu chưa đủ min frames
            if self.consecutive_speech_frames < self.min_speech_frames:
                logger.debug(f"🎤 Chunk {self.total_chunks}: SPEECH ✅ (buffering {self.consecutive_speech_frames}/{self.min_speech_frames})")
                return
            
            # Nếu đủ min frames và chưa trong speech segment
            if not self.in_speech_segment:
                logger.debug(f"🎯 Speech segment started! Adding buffered chunks ({len(self.chunk_buffer)} pre-speech + {len(self.temp_speech_buffer)} speech)")
                
                if self.save_chunks:
                    # Thêm tất cả chunks trong pre-speech buffer
                    for buf_chunk in self.chunk_buffer:
                        if len(self.accumulated_speech) == 0 or buf_chunk['chunk_id'] > self.total_chunks - len(self.temp_speech_buffer) - len(self.chunk_buffer):
                            self.accumulated_speech.append(buf_chunk['audio'])
                            self.speech_count += 1
                    
                    # Thêm tất cả chunks trong temp speech buffer
                    for temp_chunk in self.temp_speech_buffer:
                        self.accumulated_speech.append(temp_chunk)
                        self.speech_count += 1
                
                self.temp_speech_buffer = []  # Clear temp buffer
                self.in_speech_segment = True
            else:
                # Đã trong speech segment, thêm chunk từ temp buffer
                if self.save_chunks:
                    for temp_chunk in self.temp_speech_buffer:
                        self.accumulated_speech.append(temp_chunk)
                        self.speech_count += 1
                self.temp_speech_buffer = []
                
                logger.debug(f"🎤 Chunk {self.total_chunks}: SPEECH ✅")
            
        else:
            # Không phải speech
            self.consecutive_speech_frames = 0
            self.temp_speech_buffer = []  # Clear temp buffer khi gặp silence
            self.silent_streak += 1
            
            if self.in_speech_segment:
                # Nếu đang trong speech segment
                if self.silent_streak <= self.silent_threshold:
                    # Vẫn trong ngưỡng cho phép, thêm chunk silent này
                    if self.save_chunks:
                        self.accumulated_speech.append(original_chunk.copy())
                        self.speech_count += 1
                    logger.debug(f"🔇 Chunk {self.total_chunks}: SILENCE in speech segment ⚠️ (streak {self.silent_streak})")
                else:
                    # Vượt quá ngưỡng, kết thúc speech segment
                    self.in_speech_segment = False
                    logger.debug(f"🔇 Chunk {self.total_chunks}: SILENCE ❌ (streak {self.silent_streak}) - End of speech segment")
                    
                    # Trigger callback khi kết thúc speech segment
                    self.on_speech_segment_end()
            else:
                # Không trong speech segment, chỉ ghi log
                logger.debug(f"🔇 Chunk {self.total_chunks}: SILENCE ❌")

    def on_speech_segment_end(self):
        """Callback khi kết thúc một speech segment"""
        if self.accumulated_speech:
            speech_duration = len(self.accumulated_speech) * self.chunk_duration_ms / 1000.0
            logger.debug(f"🎯 Speech segment ended: {speech_duration:.2f}s ({len(self.accumulated_speech)} chunks)")
            
            # Ở đây bạn có thể xử lý speech segment (gửi để translation, etc.)
            # self.process_speech_segment(self.accumulated_speech)

    def start_processing(self):
        """Bắt đầu xử lý audio stream"""
        self.is_processing = True
        processing_thread = threading.Thread(target=self._processing_loop, daemon=True)
        processing_thread.start()
        
        # Bắt đầu phát audio
        if self.enable_playback and self.audio_player:
            self.audio_player.start_playback()
            
        logger.info("🚀 Started real-time audio processing")

    def stop_processing(self):
        """Dừng xử lý audio stream"""
        self.is_processing = False
        
        # Dừng phát audio
        if self.enable_playback and self.audio_player:
            self.audio_player.stop_playback()
            
        logger.info("⏹️ Stopped audio processing")

    def _processing_loop(self):
        """Vòng lặp xử lý audio chunks từ queue"""
        while self.is_processing:
            try:
                # Lấy chunk từ queue (timeout 0.1s)
                if not self.audio_queue.empty():
                    chunk_data = self.audio_queue.get(timeout=0.1)
                    self.process_chunk(chunk_data)
                else:
                    time.sleep(0.001)  # Sleep ngắn để không waste CPU
            except Exception as e:
                logger.error(f"Error processing chunk: {e}")
                time.sleep(0.001)

    def get_speech_audio(self):
        """Lấy tất cả audio speech đã được xử lý"""
        if self.accumulated_speech:
            return np.concatenate(self.accumulated_speech)
        return np.array([])

    def save_speech_audio(self, filename="speech_realtime_10ms.wav"):
        """Lưu audio speech ra file"""
        speech_audio = self.get_speech_audio()
        if len(speech_audio) > 0:
            # Convert to int16 for wav file
            audio_int16 = (speech_audio * 32767).astype(np.int16)
            wavfile.write(filename, self.sr, audio_int16)
            duration = len(speech_audio) / self.sr
            logger.info(f"💾 Saved {filename} ({duration:.2f} seconds)")
            return True
        else:
            logger.info("❌ No speech audio to save")
            return False

    def get_batched_chunks(self, chunks_per_batch=3, clear_processed=True):
        """
        Lấy ra danh sách các chunks đã được ghép theo batching config.
        
        Args:
            chunks_per_batch (int): Số lượng chunks cần ghép thành một batch (ví dụ: 3 chunks 10ms = 30ms)
            clear_processed (bool): Có xóa các chunks đã lấy ra khỏi buffer hay không
            
        Returns:
            list: Danh sách các chunks đã được ghép, mỗi phần tử là một np.array.
                 Trả về [] nếu không đủ số lượng chunks tối thiểu.
        
        Example:
            Nếu có 32 chunks trong buffer và chunks_per_batch=3:
            - Sẽ tạo ra 10 batched chunks (30 chunks được sử dụng)  
            - Còn lại 2 chunks chưa đủ số lượng sẽ được giữ lại trong buffer
        """
        total_chunks = len(self.processed_chunks_buffer)
        
        # Kiểm tra có đủ chunks để tạo ít nhất 1 batch không
        if total_chunks < chunks_per_batch:
            # logger.debug(f"📦 Not enough chunks for batching: {total_chunks}/{chunks_per_batch}")
            return []
        
        # Tính số complete batches có thể tạo
        num_complete_batches = total_chunks // chunks_per_batch
        chunks_to_extract = num_complete_batches * chunks_per_batch
        
        # logger.info(f"📦 Batching: {total_chunks} total chunks → {num_complete_batches} batches of {chunks_per_batch} chunks each")
        # logger.info(f"📦 Will extract {chunks_to_extract} chunks, keep remaining {total_chunks - chunks_to_extract}")
        
        batched_chunks = []
        extracted_chunks = []
        
        # Lấy chunks từ buffer
        for _ in range(chunks_to_extract):
            if clear_processed:
                chunk = self.processed_chunks_buffer.popleft()  # Lấy và xóa
            else:
                chunk = self.processed_chunks_buffer[len(extracted_chunks)]  # Chỉ lấy, không xóa
            extracted_chunks.append(chunk)
        
        # Ghép chunks thành batches
        for i in range(num_complete_batches):
            start_idx = i * chunks_per_batch
            end_idx = start_idx + chunks_per_batch
            
            batch_chunks = extracted_chunks[start_idx:end_idx]
            batched_chunk = np.concatenate(batch_chunks)
            batched_chunks.append(batched_chunk)
            
            # Log thông tin batch
            batch_duration_ms = len(batched_chunk) / self.sr * 1000
            logger.debug(f"📦 Created batch {i+1}: {len(batched_chunk)} samples ({batch_duration_ms:.1f}ms)")
        
        return batched_chunks

    def get_processed_chunks_count(self):
        """Lấy số lượng chunks đã được xử lý hiện có trong buffer"""
        return len(self.processed_chunks_buffer)

    def clear_processed_chunks(self):
        """Xóa tất cả chunks đã xử lý trong buffer"""
        count = len(self.processed_chunks_buffer)
        self.processed_chunks_buffer.clear()
        logger.info(f"🧹 Cleared {count} processed chunks from buffer")
        return count
        
    def get_statistics(self):
        """Lấy thống kê xử lý"""
        speech_ratio = (self.speech_count / self.total_chunks * 100) if self.total_chunks > 0 else 0
        return {
            'total_chunks': self.total_chunks,
            'speech_chunks': self.speech_count,
            'speech_ratio': speech_ratio,
            'current_state': 'IN_SPEECH' if self.in_speech_segment else 'SILENT',
            'silent_streak': self.silent_streak,
            'consecutive_speech_frames': self.consecutive_speech_frames,
            'min_speech_frames': self.min_speech_frames,
            'temp_buffer_size': len(self.temp_speech_buffer),
            'chunk_duration_ms': self.chunk_duration_ms,
            'analysis_duration_ms': self.analysis_duration_ms,
            'overlap_chunks': self.overlap_chunks,
            'processed_chunks_in_buffer': len(self.processed_chunks_buffer)  # Thêm thông tin buffer
        }
