# mezon-whisper — Chiến lược finetune STT Việt + Anh (2 tuần)

> File này là context cho Claude Code. Khi làm bất kỳ task nào liên quan đến finetune STT, hãy đọc file này trước và tuân thủ các **quyết định đã chốt** và **quy ước** bên dưới. Không tự ý thay đổi các quyết định đã chốt nếu chưa hỏi lại.

---

## 1. Bối cảnh

### 1.1. Hệ thống hiện tại

- Ghi âm cuộc gọi qua **SFU của Mezon** (không còn LiveKit). Mỗi participant có một track riêng.
- **record-service** ghi **raw PCM16 mono 16 kHz, không header** lên MinIO. Object key có dạng `{room_id}/{identity}-{source}-audio-{hex6}.pcm` (`audio-ingestion/record-service/src/record_service/infra/naming.py`).
- **audio-processing-service** transcode thêm một bản derivative `.ogg` (Opus 32 kbps), **chỉ để client nghe lại**. STT không dùng bản này.
- **STT đọc thẳng file `.pcm`** (`Architect_MultiClient_Server/stt_service/service/whisper_transcription_processor.py`, `_pcm16_bytes_to_float32`). Audio này đã đi qua codec WebRTC giữa client và SFU trước khi được decode thành PCM.
- Đặc điểm audio: **mỗi track chỉ có một người nói chính**, xen kẽ giữa đoạn nói và đoạn im lặng dài. Có thể có tiếng vọng nhỏ của người khác lọt vào mic.
- STT hiện chạy **faster-whisper `large-v3-turbo` trên CPU**, `compute_type=int8`, `beam_size=1`, `repetition_penalty=1.2`, `language="vi"`, `without_timestamps=True`, `vad_filter=False`.
- **Pipeline hiện tại (gọi là pipeline *marker*)**, trong `stt_service/service/whisper_marker_transcriber.py`:
  1. Chạy Silero VAD qua lớp gọi riêng của team (`detect_speech`, **không** dùng `vad_filter` của Whisper).
  2. Gộp nhiều span VAD thành một chunk ≤ 30s. Giữa các span chèn audio marker "dau moc hong ngoc".
  3. Decode cả chunk một lần, rồi tách token về từng span dựa vào vị trí marker (`partition_tokens`).
  4. Nếu tách thất bại thì fallback sang Gipformer.
  5. Timestamp của segment = start/end của span VAD. **Không dùng timestamp của Whisper** vì không chính xác.
- Kết quả lưu ở PostgreSQL, bảng `transcript_chunks.segments` (JSONB). Mỗi segment có `start`, `end`, `text`, `confidence`, và `metadata.{avg_logprob, compression_ratio, no_speech_prob, temperature}`.
- Bảng liên quan (`orchestrator_service/services/postgresql/models.py`):
  - `rooms` (id UUID, room_name, status, participants, created_at, completed_at)
  - `tracks` (id, track_id, room_ref_id → rooms.id, participant_identity, status, audio_info JSONB `{filename, duration_sec, started_at_ns, ended_at_ns, location, source}`)
  - `transcript_chunks` (track_ref_id → tracks.id, chunk_index, start_time, end_time, segments)

### 1.2. Vấn đề cần giải quyết

1. **Hallucination** ở đoạn im lặng hoặc nhiễu. Nguyên nhân là Whisper được train trên dữ liệu YouTube, nên sinh ra các câu kiểu "Hãy subscribe cho kênh...", "Cảm ơn các bạn đã theo dõi".
2. **Code-switching Việt–Anh** trong họp kỹ thuật, ví dụ "mình deploy lên staging rồi review PR nhé". Thuật ngữ tiếng Anh hay bị phiên âm sai.
3. **Sai tên riêng và thuật ngữ nội bộ.** Lỗi này ảnh hưởng trực tiếp tới chất lượng summary do LLM tạo ra.
4. **Pipeline marker phức tạp và cản trở finetune.** Model phải nhận ra một câu vô nghĩa, cần logic partition và fallback Gipformer. Sau finetune, model có thể "quên" marker và làm hỏng bước tách span.

### 1.3. Đã thử, không làm lại

| Cách | Kết quả / lý do |
|---|---|
| Chuyển sang large-v3-turbo | Tốt hơn, nhưng vẫn cần cải thiện |
| Tăng `beam_size` | Không hết hallucination |
| `vad_filter=True` của Whisper | **Không dùng.** Timestamp segment bị lệch nhiều |
| Dùng timestamp của Whisper | **Không dùng.** Sai |
| `initial_prompt` / hotwords | **Không dùng**, vì lo ngại bias |

---

## 2. Mục tiêu và ràng buộc

- **Mục tiêu 1 (D1–D5):** thay pipeline marker bằng **pipeline span**: VAD chạy riêng, mỗi span VAD decode độc lập theo batch trên GPU, timestamp lấy từ VAD (mục 4). Kết hợp với turbo gốc, pipeline này là **baseline mới (nhánh B)**. Song song đó, thu thập, gán nhãn và chia xong data.
- **Mục tiêu 2 (D6–D10):** ra được `mezon-whisper-large-v3-turbo` v1 (CTranslate2, chạy bằng faster-whisper), **tốt hơn nhánh B một cách đo được** trên tập test in-domain.
- **Thời gian:** 2 tuần (10 ngày làm việc).
- **GPU:** 1 × RTX 5090, 32GB VRAM (Blackwell, sm_120). Lần này GPU **dành riêng cho lab** (train + eval). Khi production chuyển lên GPU này, train sẽ chạy vào giờ thấp điểm (đêm hoặc đầu chiều).
- **Base model:** `openai/whisper-large-v3-turbo`. Đã chốt, không dùng PhoWhisper.
- **Compute type khi eval và khi chạy production trên GPU:** `float16` (xem quyết định 3.11).

### 2.1. Ba nhánh đánh giá

| Nhánh | Model | Pipeline | Vai trò |
|---|---|---|---|
| **A** | large-v3-turbo | marker (hiện tại), chạy trên GPU | Tham chiếu production hiện tại |
| **B** | large-v3-turbo | span (mục 4) | **Baseline mới** cho finetune |
| **C** | mezon-whisper v1 | span (mục 4) | Model finetune |

Cả ba nhánh chạy trên 5090, `float16`, cùng tập test, cùng `normalize.py`.

> Nhánh A: `MarkerWhisperTranscriber` đang hard-code `device="cpu"`. Trong lab cần một tham số `device` để chạy nhánh này trên GPU. Không sửa gì khác.

### 2.2. Tiêu chí chấp nhận

Các tiêu chí này được đặt **trước** khi chạy và không được sửa sau khi đã thấy kết quả.

**Pipeline span (B so với A)**, điều kiện để bỏ marker và Gipformer:

| Chỉ số | Yêu cầu |
|---|---|
| WER tổng theo cửa sổ | Không tăng quá 1 điểm tuyệt đối |
| Hallucination rate | Không tăng |
| Độ chính xác timestamp (mục 7.2) | Không kém hơn A |
| Tỉ lệ span không có text | Không có span nào bị mất text do lỗi mapping (assert = 0) |

**Model finetune (C so với B):**

| Chỉ số | Yêu cầu |
|---|---|
| WER nhóm `vi` | Giảm ≥ 10–15% tương đối |
| WER nhóm `mixed` | Giảm ≥ 15% tương đối |
| Hallucination rate | Giảm ≥ 50% tương đối |
| WER nhóm `en` và LibriSpeech/AMI | Không tăng quá 1 điểm tuyệt đối |
| Term recall | Không giảm |
| RTF (5090, float16) | Tăng ≤ 10% |

---

## 3. Các quyết định đã chốt

1. **Train trên HuggingFace, chạy bằng faster-whisper.** faster-whisper chỉ inference được. Quy trình là: train bằng `transformers` + `peft`, merge LoRA, rồi convert sang CTranslate2.
2. **Một model chung cho cả Việt, Anh và code-switching.** Không tách thành hai model riêng.
3. **Phương pháp chính: LoRA trên cả encoder và decoder.** Target modules gồm `q_proj`, `k_proj`, `v_proj`, `out_proj`, `fc1`, `fc2`. Không freeze encoder, vì domain âm học (mic hội họp, codec WebRTC) khác xa dữ liệu YouTube. Full finetune chỉ là run thứ hai, không bắt buộc.
4. **Train không có timestamp** (`predict_timestamps=False`). Inference cũng dùng `without_timestamps=True`.
5. **Token ngôn ngữ:**
   - Mẫu `en` dùng token `en`.
   - Mẫu `vi`, `mixed`, `nonspeech` dùng token `vi`.
   - Lúc train và lúc inference phải nhất quán với nhau. Inference production cố định `language="vi"`, trừ khi có quyết định khác.
6. **Bỏ marker.** Mỗi span VAD là **một input độc lập** cho Whisper. Timestamp **luôn** là start/end của span VAD. Không gộp các span cách nhau quá `min_silence` thành một segment, vì như vậy sẽ gây sai thứ tự khi sort transcript nhiều người theo `start`.
7. **VAD chạy riêng, trước Whisper**, qua lớp gọi của team. **Không bao giờ bật `vad_filter` của Whisper.**
8. **Gipformer bị loại bỏ** khi pipeline span đạt tiêu chí B so với A (mục 2.2).
9. **Train và inference dùng cùng một kiểu cắt audio**: span từ VAD của pipeline span, với padding được random hóa quanh giá trị production (mục 5.2), để model không phụ thuộc vào một bộ tham số VAD duy nhất.
10. **Có mẫu non-speech với transcript rỗng**, chiếm khoảng 5–10% tập train. Đây là cách chính để giảm hallucination.
11. **Compute type = `float16`** cho cả eval và production trên GPU. Model turbo ở fp16 chỉ khoảng 1,6GB, 32GB VRAM là thừa. fp16 cũng gần với bf16 lúc train nhất, nên tránh được sai lệch do quantize. `int8_float16` chỉ xem xét sau này nếu cần thêm throughput, và phải eval lại.
12. **Có dữ liệu tiếng Anh để chống quên** (English replay), chiếm khoảng 20% tập train.
13. **Không dùng LLM để tự sửa transcript.** LLM chỉ được dùng để *đánh dấu* câu đáng ngờ cho người xem lại.
14. **Pseudo-label chỉ dùng cho train**, không bao giờ dùng cho dev hoặc test, và chiếm tối đa khoảng 50% dữ liệu in-domain của train.
15. **Data lấy từ file `.pcm` gốc**, là đúng input của STT. Không dùng bản derivative `.ogg`.
16. **Chỉ dùng dataset công khai có license cho phép thương mại** (CC-BY, CC0, Apache...). **Không dùng Bud500 và VIVOS** (CC BY-NC-SA 4.0), kể cả cho pilot. Chốt ngày 01/10 để tránh rủi ro pháp lý cho sản phẩm.

---

## 4. Pipeline span (thay cho pipeline marker)

### 4.1. Luồng xử lý

```
PCM16 track
  → VAD (Silero, gọi riêng)        → danh sách span [start, end] (mẫu), đã pad
  → hậu xử lý span                 → tách span > max_span_s, bỏ span < min_span
  → decode batch trên GPU          → mỗi span một input, without_timestamps=True
  → map kết quả về span theo chỉ số → segment {start, end} = span VAD, text = output Whisper
  → lọc hallucination theo span    → blacklist, lặp từ, ngưỡng no_speech/logprob
```

### 4.2. Decode

Dùng `faster_whisper.BatchedInferencePipeline.transcribe(audio, clip_timestamps=[{"start": s, "end": e}, ...], ...)` với các span tính bằng giây. Khi truyền `clip_timestamps`, faster-whisper **không chạy VAD của nó** (`vad_filter` bị bỏ qua), và mỗi clip là một input riêng trong batch (đã kiểm tra trên faster-whisper 1.2.1, là version production đang pin).

```python
pipeline.transcribe(
    audio,
    clip_timestamps=spans_sec,
    language="vi",
    task="transcribe",
    without_timestamps=True,
    beam_size=5,                       # tinh chỉnh trên dev (1 hoặc 5)
    temperature=0.0,
    condition_on_previous_text=False,
    compression_ratio_threshold=2.4,
    log_prob_threshold=-1.0,
    no_speech_threshold=0.6,
    batch_size=16,
)
```

**Quy tắc mapping (bắt buộc):**

- **Không dùng `segment.start` / `segment.end` do Whisper trả về.** Mỗi output được map về span bằng `segment.seek`, là offset của clip tính theo frame (`int(offset * frames_per_second)`). Sau đó gán `start`/`end` bằng span VAD.
- Nếu một span cho ra nhiều segment thì nối text lại. Nếu span không có output thì text rỗng.
- Phải có **unit test** kiểm tra: N span vào thì đúng N segment ra, đúng thứ tự, và timestamp khớp span. Test cần có span im lặng, span rất ngắn, và span dài gần 30s.
- **`BatchedInferencePipeline` không tự bỏ đoạn non-speech.** Chạy thử ngày 01/10 trên 9 clip nhiễu: decode tuần tự trả rỗng cả 9 (nhờ `no_speech_threshold`), còn decode batch trả text cho cả 9. Vì vậy quy tắc lọc theo `no_speech_prob` / `avg_logprob` ở mục 4.4 là **bắt buộc**, pipeline span phải tự áp dụng.
- **`suppress_blank`** (mặc định `True`) chặn model sinh EOT ngay ở token đầu tiên, tức là chặn output rỗng. Model finetune học trả rỗng cho non-speech (quyết định 3.10), nên nhánh C phải thử `suppress_blank=False` trên dev và chọn theo hallucination rate + WER.
- Nếu `BatchedInferencePipeline` không đảm bảo được mapping thì chuyển sang tự batch: pad từng span thành một feature, rồi gọi `WhisperModel.generate_segment_batched` / `model.model.generate`. Không quay lại cách dùng marker.

### 4.3. VAD và tham số

- **Mô hình VAD mặc định: Silero** (asset đi kèm faster-whisper, gọi bằng `get_speech_timestamps`, **tách khỏi** `transcribe`). Đây là lựa chọn phổ biến nhất hiện nay. Độ phân giải của nó là 32ms một frame (512 mẫu ở 16kHz).
- Tham số khởi điểm (lấy từ production): `threshold=0.5`, `neg_threshold=0.35`, `min_speech_duration_ms=250`, `min_silence_duration_ms=1000`, `speech_pad_ms=250`.
- Thêm `max_speech_duration_s` khoảng 20–25s. Silero sẽ tự cắt span dài tại khoảng lặng cuối cùng > 98ms. Nếu không có khoảng lặng nào thì mới cắt cứng.
- **Đánh đổi chính là `min_silence_duration_ms`:** giá trị nhỏ cho span ngắn hơn, thứ tự hội thoại chính xác hơn, nhưng Whisper có ít ngữ cảnh hơn nên WER có thể tăng. Tinh chỉnh trên dev trong khoảng 500–1000ms, chọn theo WER + hallucination + độ chính xác timestamp.
- **Phương án thay thế (tùy chọn, chỉ làm khi còn thời gian trước D5):** `pyannote/segmentation-3.0` (tốt hơn với tiếng vọng và người nói chồng, nhưng nặng hơn) và TEN VAD. Chỉ thay Silero nếu phương án mới **thắng rõ** trên dev theo các chỉ số ở mục 7.2.

### 4.4. Hậu xử lý theo span

- Bỏ text của span nếu khớp mờ với một câu trong `labeling/hallucination_blacklist.txt`. Seed từ `HALLUCINATION_BLACKLIST` trong `whisper_marker_transcriber.py`, cộng thêm các câu grep được trong `transcript_chunks`.
- Bỏ text nếu có một từ hoặc cụm từ lặp liên tiếp quá 3–4 lần.
- Bỏ text nếu `no_speech_prob > 0.6` **và** `avg_logprob < -1.0` (quy tắc gốc của Whisper). Với pipeline span, quy tắc này có ý nghĩa hơn, vì mỗi span là một đơn vị độc lập.
- Ghi `metadata.model_version` và `metadata.pipeline = "span"` vào từng segment.

---

## 5. Pipeline dữ liệu

### 5.1. Hai loại đơn vị dữ liệu

| Loại | Dùng cho | Đơn vị | Cách gán nhãn |
|---|---|---|---|
| **Cửa sổ (window)** | test, dev | Đoạn audio liên tục 60–120s, cắt từ track gốc | Người gán nhãn **vẽ region** quanh từng câu của người nói chính, rồi gõ text cho từng region |
| **Clip** | train | Một span VAD | Nghe và sửa pre-label |

Test và dev dùng cửa sổ để **không phụ thuộc vào cách cắt của pipeline**. Nhờ đó so sánh được nhánh A/B/C, và việc tinh chỉnh VAD không làm phải gán nhãn lại.

### 5.2. Manifest (JSONL, mỗi dòng một đơn vị)

**Clip (train):**

```json
{
  "id": "trk_abc123_s0012",
  "kind": "clip",
  "audio": "data/clips/trk_abc123_s0012.wav",
  "text": "mình deploy lên staging rồi nhé",
  "lang": "vi",
  "category": "mixed",
  "duration": 4.2,
  "speaker": "user_123",
  "room_id": "6f1c...-uuid",
  "date": "2026-09-14",
  "track_id": "abc123",
  "offset_start": 132.45,
  "offset_end": 136.65,
  "source": "mezon",
  "label_type": "human",
  "prelabel": {"turbo": "...", "large_v3": "...", "agree_wer": 0.03, "avg_logprob": -0.21, "no_speech_prob": 0.02}
}
```

**Cửa sổ (test/dev):**

```json
{
  "id": "trk_def456_w003",
  "kind": "window",
  "audio": "data/windows/trk_def456_w003.wav",
  "track_id": "def456",
  "room_id": "...",
  "speaker": "user_456",
  "date": "2026-09-26",
  "offset_start": 360.0,
  "offset_end": 480.0,
  "regions": [
    {"start": 3.12, "end": 7.80, "text": "ok mình merge PR này nhé", "category": "mixed"},
    {"start": 15.40, "end": 16.10, "text": "", "category": "nonspeech"}
  ],
  "label_type": "human_reviewed"
}
```

Giá trị hợp lệ:

- `category`: `vi` | `en` | `mixed` | `nonspeech`
- `label_type`: `human` | `human_reviewed` | `pseudo` | `empty_verified`
- `source`: `mezon` | `fleurs_vi_train` | `librispeech_train` | `ami_ihm_train` | `cv_vi` | ...

Với `nonspeech`, `text` là chuỗi rỗng `""`. Trong cửa sổ, mọi phần không nằm trong region nào đều được coi là không có tiếng người nói chính.

### 5.3. `01_extract.py`: PG → MinIO → audio

1. Query `tracks` có `status='completed'`, join `rooms` để lấy `created_at`. Chỉ lấy track audio mic (lọc theo `audio_info.source`, xem giá trị bằng `01_extract.py inspect`). Lấy ngẫu nhiên nhưng đa dạng theo phòng, người nói và thời gian. Mục tiêu **40–60 giờ** audio thô làm ứng viên.
2. Object key là `audio_info.filename`, tải từ bucket `MINIO_BUCKET`.
3. Đọc PCM16 16k mono: `np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0`, dùng lại đúng hàm `_pcm16_bytes_to_float32` của STT. Lưu ra WAV 16k mono ở `data/raw/`.
4. **Chọn phòng cho test/dev trước** (mục 8), rồi mới cắt:
   - Phòng thuộc test/dev: cắt thành **cửa sổ** 60–120s. Mỗi track lấy nhiều cửa sổ rải đều, đảm bảo có cả đoạn nói lẫn đoạn im lặng hoặc tiếng vọng.
   - Phòng thuộc train: chạy VAD của pipeline span (`mezon_whisper/vad.py`) để lấy span. Khi cắt clip, **random padding** 100–400ms mỗi đầu và random `min_silence` trong khoảng 500–1000ms (quyết định 3.9). Clip ≤ 28s.
5. Ứng viên non-speech: các đoạn VAD bắt được nhưng năng lượng thấp, `no_speech_prob` cao, hoặc output khớp blacklist khi pre-label. Thêm các đoạn random nằm ngoài span VAD (im lặng hoặc nhiễu thật).
6. Ghi ra `data/manifests/candidates_clips.jsonl` và `candidates_windows.jsonl`.

`vad.py` phải **import code VAD của pipeline span**. Không viết thêm một VAD thứ hai.

### 5.4. `02_prelabel.py`: pre-label bằng hai model và phân luồng

- **Clip:** chạy qua `large-v3-turbo` và `large-v3` (faster-whisper, GPU, `float16`, `beam_size=5`, `condition_on_previous_text=False`, `without_timestamps=True`, `language="vi"`).
- **Cửa sổ:** chạy pipeline span (nhánh B) để có sẵn region + text. Người gán nhãn chỉnh lại biên region và sửa text.

Quy tắc phân luồng clip:

| Điều kiện | Luồng |
|---|---|
| WER(turbo, large_v3) < 5%, `avg_logprob > -0.3`, không khớp blacklist, không lặp | `auto_accept` → pseudo-label. Lấy ngẫu nhiên 5% đưa người kiểm tra |
| Một model ra rỗng, model kia ra text; hoặc khớp blacklist; hoặc `no_speech_prob` cao; hoặc `compression_ratio > 2.4` | `nonspeech_check` → người xác nhận |
| Có token Latin hoặc tiếng Anh xen trong câu tiếng Việt | `human_priority` |
| Các trường hợp còn lại lệch nhau | `human` |

**Thứ tự xét trong `02_prelabel.py`:** `nonspeech_check` → `human_priority` → `auto_accept` → `human`. Câu có từ tiếng Anh luôn đi vào `human_priority`, kể cả khi hai model giống nhau, vì hai model có thể cùng phiên âm sai một thuật ngữ và code-switching là điểm yếu chính cần nhãn người. Pre-label hiển thị cho người gán nhãn lấy từ `large-v3`.

Ngoài các span VAD, mỗi track còn lấy thêm khoảng 8% clip ngẫu nhiên (2–10 giây) nằm hẳn trong khoảng lặng giữa các span, đưa vào `nonspeech_check`. Đây là nguồn mẫu non-speech thật (nhiễu nền, tiếng vọng) mà VAD đã loại.

### 5.5. `06_prepare_public.py`: dữ liệu công khai (quy mô vừa phải)

- **Chỉ dùng dataset cho phép thương mại** (quyết định 3.16). Đã tải ngày 30/09 bằng `00_download_public.py`:

  | Nhóm | Tập | Ngôn ngữ | Clip | Giờ | License |
  |---|---|---|---|---|---|
  | train | FLEURS vi_vn train + validation | vi | 3349 | 10,21 | CC-BY 4.0 |
  | train | LibriSpeech train.100 (1 shard) | en | 2039 | 7,29 | CC-BY 4.0 |
  | train | AMI ihm train (2 shard) | en | 4561 | 4,25 | CC-BY 4.0 |
  | test | FLEURS vi_vn test | vi | 850 | 2,94 | CC-BY 4.0 |
  | test | FLEURS en_us test | en | 647 | 1,77 | CC-BY 4.0 |
  | test | LibriSpeech test-clean | en | 2611 | 5,32 | CC-BY 4.0 |
  | test | AMI ihm test (1 shard) | en | 2744 | 1,81 | CC-BY 4.0 |

- **Tiếng Việt công khai chỉ có khoảng 10 giờ** (FLEURS vi, giọng đọc). Có thể bổ sung Common Voice vi (CC0) nếu tải được từ Mozilla Data Collective; bộ này nhỏ. **Không dùng Bud500, VIVOS** (CC BY-NC-SA 4.0).
- **Tiếng Anh:** tăng lên khoảng 20 giờ bằng cách tải thêm shard AMI ihm (hội thoại họp, ưu tiên) và LibriSpeech.
- Câu chỉ gồm từ đệm (`mm-hmm`, `uh`...) của AMI đã bị loại khi export, theo đúng guideline mục 6.
- **Tập test công khai cố định** (để đo baseline và kiểm tra quên kiến thức cũ): FLEURS vi_vn test, LibriSpeech test-clean (subset), AMI ihm test (subset).
- **Augmentation:** áp dụng cho khoảng 50% mẫu, để mô phỏng đường đi client → WebRTC → SFU → PCM.
  - Encode qua Opus **kiểu WebRTC** với bitrate ngẫu nhiên 16–48 kbps: `ffmpeg -i x.wav -c:a libopus -application voip -b:a <br> tmp.opus`, rồi decode lại về PCM 16k mono. **Không** dùng Opus 32k của bản derivative, vì STT không đọc bản đó.
  - Thêm nhiễu nền SNR 10–30 dB. Nguồn nhiễu là các đoạn im lặng trích từ chính audio Mezon.
- Chuẩn hóa text của dữ liệu công khai theo **cùng quy ước** ở mục 6.
- Kiểm tra license từng bộ dữ liệu trước khi dùng cho sản phẩm.

### 5.6. Tỉ lệ trộn trong tập train

Tính theo số giờ mỗi epoch.

| Nhóm | Tỉ lệ |
|---|---|
| In-domain Mezon (human + pseudo, oversample ×2–3) | ~55–60% |
| Tiếng Việt công khai (FLEURS vi ~10h, không oversample quá ×2) | ~10–15% |
| Tiếng Anh công khai | ~20% |
| Non-speech (`empty_verified`) | ~5–10% |

Tổng mỗi run: **60–100 giờ** (đã tính oversample), để một run chạy xong trong một đêm trên 5090.

**Hệ quả của việc bỏ data NC:** tiếng Việt công khai chỉ còn khoảng 10 giờ giọng đọc, nên model phụ thuộc nhiều hơn vào data Mezon. Mục tiêu **10–15 giờ nhãn người + pseudo-label in-domain** trở thành điều kiện chính để v1 đạt tiêu chí, không còn là phần có thể cắt xuống 5 giờ. Rủi ro overfit vào ít người nói tăng lên, nên cần theo dõi WER nhóm "người chưa thấy" (mục 8).

---

## 6. Hướng dẫn gán nhãn

Nội dung này được đưa nguyên vào `labeling/guideline.md`.

- **Ghi đúng những gì được nói.** Không sửa ngữ pháp, không thêm từ người nói bỏ sót.
- **Từ tiếng Anh giữ chính tả gốc:** "deploy", "pull request", "staging". Viết tắt đọc từng chữ thì viết hoa liền: "API", "CI", "PR". Tên sản phẩm giữ cách viết chính thức: "Mezon", "MinIO".
- **Số:** ghi bằng chữ số, ví dụ "20", "3 giờ", "v2".
- **Từ đệm** ("ờ", "à", "ừm", "uh"): bỏ. Một region chỉ có từ đệm thì để text rỗng và category `nonspeech`.
- **Nói lắp, nói lại** ("mình mình mình nghĩ là"): ghi một lần phần có nghĩa ("mình nghĩ là"). *(Quy tắc này do Claude thêm vào `guideline.md` ngày 02/10, chờ Lead xác nhận.)*
- **Chỉ ghi người nói chính của track.** Tiếng vọng của người khác thì không ghi. Nếu một clip chỉ có tiếng vọng, xếp vào `nonspeech` với text rỗng.
- **Chỗ không nghe rõ:** đánh dấu `[?]`. Region hoặc clip có `[?]` bị loại khỏi phần tính WER của test/dev.
- **Dấu câu:** giữ dấu câu cơ bản (`.` `,` `?`). Khi tính WER sẽ chuẩn hóa bỏ đi.
- **Chính tả tiếng Việt:** lưu ở **Unicode NFC**. Dùng **kiểu đặt dấu thanh mới** ("hoà", "thuỷ", "khoẻ"). *(Đổi dòng này nếu team chọn kiểu cũ, nhưng phải thống nhất.)*
- **Chọn `category`** cho mỗi clip hoặc region: `vi` / `en` / `mixed` / `nonspeech`. Có checkbox "audio lỗi, bỏ".
- **Riêng với cửa sổ (test/dev):** vẽ region **sát** với lúc bắt đầu và kết thúc câu, sai số ≤ 100ms. Hai câu cách nhau ≥ 0,5s thì tách thành hai region. Biên region được dùng để đo độ chính xác timestamp.

Công cụ: **Label Studio**, dùng hai template:

- Clip: Audio Transcription (`<Audio>` + `<TextArea>` + `<Choices>` category).
- Cửa sổ: Audio + `<Labels>` + `<TextArea perRegion="true">` + `<Choices perRegion="true">`.

Pre-label được import sẵn, người gán nhãn chỉ nghe và sửa.

**Tập test:** cần hai lượt, một người sửa và một người khác review. Nhãn đạt yêu cầu có `label_type = human_reviewed`.

---

## 7. Đánh giá

### 7.1. Chuẩn hóa text (`mezon_whisper/normalize.py`)

Áp dụng giống hệt nhau cho reference và hypothesis:

1. Chuẩn hóa Unicode NFC, rồi chuyển về chữ thường.
2. Map kiểu đặt dấu thanh về một chuẩn duy nhất ("hòa" → "hoà", ...).
3. Chuẩn hóa số theo quy ước ở mục 6.
4. Bỏ dấu câu (thay bằng khoảng trắng; `\w` vẫn giữ chữ có dấu).
5. Gộp các khoảng trắng liên tiếp.

### 7.2. Chỉ số (`05_eval.py`)

Mỗi nhánh (A/B/C) chạy **nguyên track** của các phòng test/dev qua pipeline của nó. Sau đó cắt output theo từng cửa sổ: mỗi segment được gán cho cửa sổ chứa trung điểm của nó.

| Chỉ số | Cách tính |
|---|---|
| **WER cửa sổ (chỉ số chính)** | Nối text reference của cửa sổ theo thời gian, nối text hypothesis theo thời gian, rồi tính `jiwer` **gộp theo corpus**. Chỉ số này không phụ thuộc vào cách cắt |
| WER theo nhóm `vi` / `en` / `mixed` | Gán mỗi segment hypothesis cho region reference có overlap thời gian lớn nhất, rồi tính WER gộp theo category của region |
| CER theo nhóm | `jiwer.cer`, dùng để bắt lỗi sai dấu thanh |
| Tách S / D / I | `jiwer.process_words`. Insertion cao là dấu hiệu hallucination |
| **Hallucination rate** | (a) Tỉ lệ region `nonspeech` có output khác rỗng. (b) Số từ hypothesis nằm ngoài mọi region có tiếng, tính trên mỗi phút audio im lặng |
| **Độ chính xác timestamp** | Với mỗi region reference: \|start_hyp − start_ref\| và \|end_hyp − end_ref\|, báo cáo median và p90. Kèm theo **boundary F1** với dung sai 300ms |
| **Lỗi thứ tự** | Trên cả phòng: trộn segment của mọi track, sort theo `start`, đếm số cặp bị đảo thứ tự so với reference (Kendall tau). Đo đúng cái side effect mà pipeline marker và việc gộp span gây ra |
| Term recall | Với từng term trong `resources/terms.txt` có xuất hiện trong reference: tỉ lệ term đó cũng xuất hiện đúng trong hypothesis |
| RTF | Thời gian xử lý ÷ thời lượng audio, đo trên 5090, `float16` |
| Khoảng tin cậy | Bootstrap 1000 lần theo cửa sổ, tính trên WER từng nhóm |
| Kiểm tra quên kiến thức cũ | WER trên LibriSpeech test-clean (subset) và AMI test (subset), chạy qua pipeline span |

Output của script:

- File `reports/<nhánh>_<model>.md`, gồm bảng chỉ số và 50 cửa sổ có WER cao nhất (ref / hyp).
- File JSON chứa toàn bộ kết quả, để so sánh các nhánh với nhau.

### 7.3. Nguyên tắc bắt buộc khi đánh giá

- **Luôn đánh giá bằng đúng pipeline sẽ chạy production** (VAD + decode + hậu xử lý), không gọi `transcribe()` trực tiếp trên từng clip.
- Model mới phải được đánh giá **sau khi convert sang CTranslate2**, ở `float16`. Không kết luận dựa trên WER đo bằng HF lúc train.
- **Tập test đóng băng.** Không dùng để chọn checkpoint, tinh chỉnh VAD hay tham số decode. Mọi việc tinh chỉnh đều làm trên dev.

---

## 8. Chia tập (`04_split.py`)

- **Không chia ngẫu nhiên theo clip.** Chia theo `room_id` (room-disjoint).
- **Chọn phòng test/dev trước khi cắt** (mục 5.3), vì hai tập này được cắt thành cửa sổ, còn train được cắt thành clip.
- **Test (~2 giờ audio cửa sổ):** lấy từ các phòng gần đây nhất. Nếu được, giữ vài người nói không xuất hiện trong train. Báo cáo riêng hai nhóm "người đã thấy" và "người chưa thấy".
- **Dev (~1 giờ):** room-disjoint với cả train và test. Dùng cho tinh chỉnh VAD, tham số decode và chọn checkpoint.
- **Train:** phần còn lại, gồm human, pseudo và empty_verified.
- **Cơ cấu mục tiêu của test** (tính theo thời lượng region): khoảng 50% `vi`, 20% `mixed`, 15% `en`, 15% `nonspeech`. Nếu `en` ít quá thì ghi rõ trong báo cáo, không ép.
- Script phải **assert** không có `room_id` nào xuất hiện ở nhiều hơn một tập, và ghi danh sách phòng ra `data/manifests/split_rooms.json` (commit file này để đóng băng split).

---

## 9. Huấn luyện (`07_train.py`)

### 9.1. Môi trường cho RTX 5090 (Blackwell)

- PyTorch build với **CUDA 12.8 trở lên** (`torch>=2.7`, index `cu128`).
- `faster-whisper==1.2.1` (cùng version production), `ctranslate2` có hỗ trợ CUDA 12 và sm_120.
- Thư viện: `transformers`, `peft`, `accelerate`, `datasets`, `evaluate`, `jiwer`, `soundfile`, `librosa`, `audiomentations`, `bitsandbytes` (chỉ cần cho full finetune với AdamW 8-bit), `label-studio`, `minio`, `psycopg`.
- **Ngày 1 phải chạy thử 3 thứ:**
  1. faster-whisper `BatchedInferencePipeline` trên GPU, `float16`, có `clip_timestamps`.
  2. Một bước train HF + PEFT.
  3. `ct2-transformers-converter`, rồi load lại model đã convert bằng faster-whisper.

  Hỗ trợ Blackwell của CTranslate2 và bitsandbytes phụ thuộc vào phiên bản, nên nếu có lỗi thì phải xử lý ngay trong ngày 1.

- Smoke test **không cần dataset**. Chỉ cần 5–10 file audio bất kỳ; bước train dùng khoảng 16 mẫu, chỉ cần thấy loss giảm.
- Ghi log GPU **trong lúc chạy job** (benchmark, pilot, train), mỗi job một file, tắt khi job xong. Không bật khi GPU rảnh, vì log toàn 0% không có ý nghĩa. Khi train, GPU util < 80% kéo dài là dấu hiệu nghẽn dataloader:
  `nvidia-smi --query-gpu=timestamp,utilization.gpu,memory.used,power.draw --format=csv -l 10 >> reports/gpu_<job>.csv`

### 9.1b. Pilot run (D1 qua đêm, chỉ dùng data công khai)

- **Mục đích:** chạy `07_train.py` từ đầu đến cuối, đo throughput (giờ audio / giờ train) để biết 80–120h có vừa một đêm không, và thử merge → convert → eval. **Không phải model ứng viên.** Không đưa pilot vào so sánh A/B/C.
- **Data:** nhóm `pilot` đã tải (mục 5.5): 21,75 giờ, gồm 10,21h vi + 11,54h en. Không augment. Giữ lại 300 clip làm dev.
- **Cấu hình:** giống Run 1 (mục 9.4), 3 epoch, `--eval-steps 100`. Với ~9.600 clip, mỗi epoch ước tính 15–30 phút, nên pilot mất khoảng 1–1,5 giờ, **không cần chạy qua đêm**. Sau đó `08_convert.sh` rồi đo WER trên các tập test công khai bằng `bench_rtf.py --model`.
- **Output:** `reports/pilot.md` gồm throughput, VRAM peak, WER trước/sau trên FLEURS vi và LibriSpeech (để có cảm giác về độ quên), cùng các lỗi gặp khi merge/convert.

### 9.1c. Benchmark RTF (D1)

- `scripts/bench_rtf.py`: đo turbo gốc trên cùng một bộ audio (30–60 phút từ tập test công khai) ở 3 cấu hình: CPU `int8` (production hiện tại), GPU `float16` tuần tự, GPU `float16` batch (`BatchedInferencePipeline` + `clip_timestamps`).
- Output: `reports/rtf_gpu_vs_cpu.md`. Đây là số liệu để trình bày lý do dùng GPU cho production.

### 9.2. Đặc thù của large-v3-turbo

- Encoder có 32 layer, **decoder chỉ có 4 layer**.
- Dùng **128 mel bins**.

### 9.3. Chuẩn bị nhãn

- Gọi `tokenizer.set_prefix_tokens(language=<vi|en>, task="transcribe", predict_timestamps=False)` **cho từng mẫu**, theo quy tắc ở mục 3.5.
- Text rỗng sẽ cho labels chỉ gồm prefix và EOT. Đây là hành vi mong muốn.
- Data collator: pad `input_features`, pad `labels` bằng `-100`, bỏ token SOT ở đầu labels nếu đã có (theo blog "Fine-Tune Whisper" của HuggingFace).

### 9.4. Run 1: LoRA (cấu hình mặc định)

| Tham số | Giá trị |
|---|---|
| LoRA | `r=32`, `lora_alpha=64`, `lora_dropout=0.05`, `bias="none"` |
| target_modules | `q_proj, k_proj, v_proj, out_proj, fc1, fc2` (encoder + decoder) |
| learning_rate | `1e-4`, warmup 300 steps, scheduler linear |
| batch | `per_device_train_batch_size=16`, `gradient_accumulation_steps=2` (hiệu dụng 32) |
| epochs | 2–4, chọn theo WER trên dev (early stopping) |
| precision | `bf16=True`, `gradient_checkpointing=True`, `use_cache=False` |
| SpecAugment | `apply_spec_augment=True`, `mask_time_prob=0.05` |
| eval | mỗi 500 steps trên **dev-subset khoảng 500 region** (cắt theo biên region), `predict_with_generate=True`, metric `wer` (dùng `normalize.py`). Đây chỉ là tín hiệu nhanh; kết luận cuối dùng `05_eval.py` |
| khác | `remove_unused_columns=False`, `label_names=["labels"]`, `report_to="tensorboard"` |
| hook | `model.model.encoder.conv1.register_forward_hook(lambda m,i,o: o.requires_grad_(True))` (cần thiết khi dùng gradient checkpointing với PEFT) |
| dataloader | `num_workers` ~6 (server chỉ có 30GB RAM), đọc audio lazy từ ổ, không load cả dataset vào RAM. Theo dõi GPU util; nếu < 80% vì nghẽn dataloader thì cân nhắc tính trước log-mel ra ổ |

### 9.5. Run 2 (tùy chọn): LoRA đã chỉnh hoặc full finetune

- **LoRA đã chỉnh:** điều chỉnh tỉ lệ trộn hoặc learning rate dựa trên phân tích lỗi của run 1.
- **Full finetune:**
  - `optim="adamw_bnb_8bit"`, `learning_rate=5e-6` đến `1e-5`, `bf16`, gradient checkpointing, batch khoảng 8 với gradient accumulation 4.
  - Chỉ chạy nếu có ≥ 15 giờ in-domain có nhãn người.
  - Theo dõi sát WER tiếng Anh để phát hiện model quên kiến thức cũ.

### 9.6. Dấu hiệu cần dừng hoặc chỉnh

| Dấu hiệu | Cách xử lý |
|---|---|
| WER tiếng Anh (LibriSpeech/AMI) tăng > 1 điểm | Tăng tỉ lệ tiếng Anh hoặc giảm learning rate |
| WER dev tăng trở lại | Overfit, chọn checkpoint trước đó |
| Hallucination rate không giảm | Tăng mẫu non-speech, kiểm tra lại nhãn rỗng |
| WER trên span ngắn (< 2s) cao bất thường | Tăng tỉ lệ clip ngắn trong train |

---

## 10. Convert và deploy (`08_convert.sh`)

```bash
# 1. merge LoRA (Python)
#    merged = model.merge_and_unload()
#    merged.save_pretrained("models/mezon-whisper-hf")
#    processor.save_pretrained("models/mezon-whisper-hf")

# 2. convert sang CTranslate2
ct2-transformers-converter \
  --model models/mezon-whisper-hf \
  --output_dir models/mezon-whisper-ct2 \
  --copy_files tokenizer.json preprocessor_config.json \
  --quantization float16
```

- **BẮT BUỘC copy `preprocessor_config.json`**, vì đây là nơi khai báo 128 mel. Nếu thiếu, faster-whisper dùng 80 mel và model cho ra kết quả rác.
- **Khi deploy** (lúc production đã chuyển lên GPU):
  - Pipeline span thay pipeline marker trong STT service. Gỡ Gipformer khỏi dependency.
  - Ghi `model_version` và `pipeline` vào `metadata` của từng segment trong `transcript_chunks`.
  - Giữ model và pipeline cũ để có thể rollback bằng cách đổi config.
- **So sánh summary trước khi chuyển 100% traffic:** lấy 10–20 phòng, tạo summary từ transcript của nhánh B và C, đánh giá mù xem bản nào đúng hơn.

---

## 11. Phân vai và kế hoạch 2 tuần

### 11.1. Phân vai (cập nhật 02/10)

**Ràng buộc:** chỉ Lead được giữ secret production (PostgreSQL, MinIO) và được vào server GPU. Ba thành viên còn lại không được cấp các quyền này. Thứ duy nhất chia sẻ cho họ là **audio đã trích xuất + hướng dẫn gán nhãn**.

| Vai | Phụ trách |
|---|---|
| **Lead** (1 người) | Mọi việc chạm vào server và secret: extract, chia tập, cắt audio, pre-label, pipeline span, eval, train, convert. Dựng Label Studio và import/export nhãn. Code do Claude Code viết, Lead chạy và kiểm tra |
| **Người gán nhãn** (3 người: L1, L2, L3) | Gán nhãn test, dev, train trên Label Studio. Review chéo tập test. Gom thuật ngữ/tên riêng cho `terms.txt`. Chấm mù summary (D9). Đọc danh sách câu lỗi nặng và phân loại lỗi |

Hệ quả:

- **Nút thắt là thời gian của Lead**, không còn là công gán nhãn. Ba người gán nhãn gần như toàn thời gian cho khoảng 15–18 giờ công mỗi ngày, nên toàn bộ ngân sách gán nhãn (mục 11.5) xong trong khoảng 3 ngày.
- Vì công gán nhãn dư, **tăng nhãn người của train lên 15 giờ trở lên**. Điều này bù cho việc tiếng Việt công khai chỉ có khoảng 10 giờ (mục 5.6).
- Việc Lead phải làm trước tiên là **đưa được audio vào Label Studio**. Mỗi ngày chậm ở bước này là ba người ngồi chờ.
- Label Studio chạy trên máy Lead quản lý (server GPU hoặc máy nội bộ), người gán nhãn truy cập qua trình duyệt bằng tài khoản riêng. Không ai ngoài Lead cần đăng nhập server.
- Cắt khỏi phạm vi 2 tuần: so sánh VAD thay thế (pyannote, TEN VAD), Run 2, port vào STT service. Chuyển sang backlog (mục 15).

**GPU đang bị dùng chung:** ngày 02/10 có một tiến trình của người khác chiếm 18,5GB VRAM. Trước các lần train qua đêm (D8) phải thống nhất lịch với người đó. Không đo RTF khi GPU đang có job khác.

### 11.2. Lịch tổng (cập nhật 02/10)

| Ngày | Ngày thật | Lead | Người gán nhãn |
|---|---|---|---|
| D1 | T4 30/09 | ✅ Môi trường GPU, 3 smoke test, tải data công khai | |
| D2–D3 | T5 01/10 – T6 02/10 | ✅ Viết script: `bench_rtf`, `07_train`, `08_convert`, `01_extract`, pipeline span + test. `01_extract.py inspect` và `download --dry-run` | Đọc guideline, gom `terms.txt` |
| D4 | T2 05/10 | **Chốt consent.** `01_extract.py download`, `04_split.py`, cắt cửa sổ test/dev + clip train, pre-label trên GPU, dựng Label Studio và import | Chiều: bắt đầu gán nhãn test |
| D5 | T3 06/10 | Benchmark RTF, WER baseline data công khai, pilot LoRA. Viết `05_eval.py` | Gán nhãn test (lượt 1 + review chéo) và dev |
| D6 | T4 07/10 | `marker_adapter.py` (nhánh A). Tinh chỉnh VAD và decode trên dev | Xong test + dev. Bắt đầu train (`human_priority`, `nonspeech_check`) |
| D7 | T5 08/10 | **Đo nhánh A và B trên test** → quyết định bỏ marker. Chạy thử toàn chuỗi với data thật nhỏ | Gán nhãn train |
| D8 | T6 09/10 | Chốt `train.jsonl`. **Run 1 (LoRA) chạy qua đêm / cuối tuần** | Gán nhãn train tới trưa, sau đó kiểm tra ngẫu nhiên 5% pseudo-label |
| D9 | T2 12/10 | Convert, eval nhánh C trên test + kiểm tra quên kiến thức cũ. Tạo summary từ transcript nhánh B và C | Chấm mù summary 10–20 phòng. Phân loại 50 cửa sổ lỗi nặng nhất |
| D10 | T3 13/10 | Viết `reports/v1.md` (bảng A/B/C, bài học, backlog v2) | |

**Việc chặn đường:** consent (chặn D4), và GPU dùng chung (chặn D5, D8).

### 11.3. Trạng thái script (cập nhật 02/10)

| Script | Trạng thái |
|---|---|
| `00_server_check.sh`, `00_smoke_test.py`, `00_download_public.py` | Đã chạy trên server |
| `mezon_whisper/{normalize,manifest,vad,span_transcriber}.py`, `tests/test_span_mapping.py` | 11 test qua trên máy local (CPU, `base.en`). Chưa chạy trên GPU với turbo |
| `04_split.py`, `02_prelabel.py`, `03_labelstudio.py` | Đã chạy cả chuỗi trên máy local với track giả dựng từ audiobook tiếng Anh. Chưa chạy với data Mezon. **Chưa import thử vào một Label Studio thật** |
| `bench_rtf.py` | Đã chạy trên máy local (CPU). Chưa chạy trên GPU |
| `01_extract.py`, `07_train.py`, `08_convert.sh` | Mới kiểm tra cú pháp, chưa chạy |
| `05_eval.py` | Chưa viết. Cần trước D5 |
| `marker_adapter.py` | Chưa viết. Cần trước D6 |
| `06_prepare_public.py`, script gộp `train.jsonl` | Chưa viết. Cần trước D8 |

### 11.3b. Lệnh cho D4 (đưa audio vào Label Studio)

```bash
cd ~/mezon-whisper && source .venv/bin/activate
python scripts/01_extract.py --env-file .env inspect
python scripts/01_extract.py --env-file .env download --source <giá trị> --hours 50 --confirm-consent
python scripts/04_split.py                      # ghi split_rooms.json, sau đó split bị đóng băng
python scripts/02_prelabel.py windows           # test 2h + dev 1h, pre-label bằng pipeline span
python scripts/02_prelabel.py clips             # clip train, pre-label bằng turbo + large-v3, phân luồng
python scripts/03_labelstudio.py export         # ghi labelstudio/*.json

uv pip install label-studio
LABEL_STUDIO_LOCAL_FILES_SERVING_ENABLED=true \
LABEL_STUDIO_LOCAL_FILES_DOCUMENT_ROOT=$HOME/mezon-whisper \
  label-studio start --port 8080
```

Trong Label Studio, tạo các dự án sau. Mỗi dự án: dán labeling config, vào Settings → Cloud Storage thêm **Local Files** trỏ tới `~/mezon-whisper/data`, rồi Import file JSON tương ứng.

| Dự án | Config | File import |
|---|---|---|
| test windows | `labeling/ls_window.xml` | `labelstudio/windows_test.json` |
| dev windows | `labeling/ls_window.xml` | `labelstudio/windows_dev.json` |
| train: nonspeech_check | `labeling/ls_clip.xml` | `labelstudio/clips_nonspeech_check.json` |
| train: human_priority | `labeling/ls_clip.xml` | `labelstudio/clips_human_priority.json` |
| train: human | `labeling/ls_clip.xml` | `labelstudio/clips_human.json` |
| kiểm tra pseudo-label | `labeling/ls_clip.xml` | `labelstudio/clips_auto_accept_sample.json` |

Lấy nhãn về: trong dự án bấm Export → JSON, rồi:

```bash
python scripts/03_labelstudio.py import windows <file export>   # ghi test.jsonl / dev.jsonl
python scripts/03_labelstudio.py import clips <file export>     # ghi labeled_clips.jsonl
```

Cửa sổ test chỉ được coi là `human_reviewed` khi người review đã tick `reviewed`.

### 11.4. Lệnh cho D5 (khi GPU rảnh)

```bash
# Benchmark RTF: CPU int8 (production) so với GPU fp16
python scripts/bench_rtf.py --manifest data/manifests/public_test_fleurs_vi_test.jsonl --minutes 30
# WER baseline trên 4 tập test công khai
python scripts/bench_rtf.py --minutes 0 --out reports/public_baseline.md \
  --configs gpu_fp16_beam1_batched gpu_fp16_beam5_batched --manifest data/manifests/public_test_*.jsonl
# Pilot LoRA (~1–1,5 giờ), convert, đo lại
python scripts/07_train.py --out models/runs/pilot --epochs 3 --eval-steps 100 \
  --train data/manifests/public_pilot_*.jsonl 2>&1 | tee pilot.log
bash scripts/08_convert.sh models/runs/pilot/best models/pilot
python scripts/bench_rtf.py --model models/pilot-ct2 --minutes 0 --out reports/pilot_public.md \
  --configs gpu_fp16_beam5_batched --manifest data/manifests/public_test_*.jsonl
```

### 11.5. Ngân sách gán nhãn

Ước tính khi sửa pre-label: khoảng 2–2,5 giờ công mỗi giờ audio cho clip, khoảng 3 giờ công mỗi giờ audio cho cửa sổ (vì phải chỉnh biên region).

| Hạng mục | Audio | Giờ công |
|---|---|---|
| Test (cửa sổ, review hai lượt) | ~2 giờ | ~8 |
| Dev (cửa sổ) | ~1 giờ | ~3 |
| Train (clip, người sửa) | 15–20 giờ | ~35–50 |
| **Tổng** | | **~46–61** (3 người × khoảng 3 ngày) |

Nếu nhân lực ít, ưu tiên theo thứ tự: test → dev → train `human_priority`/`nonspeech_check`, và dựa nhiều hơn vào pseudo-label. Không nên giảm nhãn người của train xuống dưới khoảng 8 giờ, vì tiếng Việt công khai chỉ có khoảng 10 giờ (mục 5.6). **Không giảm chất lượng test.**

---

## 12. Được cắt / không được cắt

**Được cắt khi thiếu thời gian:**

- Kích thước test (tối thiểu 2 giờ).
- So sánh VAD thay thế (pyannote, TEN VAD).
- Các bộ dữ liệu công khai lớn như GigaSpeech 2.
- Run 2.
- Dò tham số nhiều vòng.

**Không được cắt:**

- Test chia theo phòng, gán nhãn theo cửa sổ và được review hai lượt.
- Đánh giá cả ba nhánh A/B/C trên cùng tập test.
- Unit test mapping span → segment của pipeline span.
- Chuẩn hóa text (NFC, kiểu đặt dấu thanh) trước khi tính WER.
- Mẫu non-speech có transcript rỗng.
- Dữ liệu tiếng Anh chống quên.
- Cắt data train bằng đúng VAD của pipeline span.
- Copy `preprocessor_config.json` khi convert.
- Đánh giá bằng đúng pipeline sau khi convert, ở `float16`.

---

## 13. Thông tin cần bổ sung

Cập nhật mục này khi có thông tin.

- [x] Code VAD hiện tại: `stt_service/service/whisper_marker_transcriber.py` (`detect_speech`, `make_vad_options`). Silero qua `faster_whisper.vad.get_speech_timestamps`; threshold 0.5, neg_threshold 0.35, min_speech 250ms, min_silence 1000ms, pad 250ms, max 30s
- [x] Tham số `transcribe()` production: CPU, `int8`, `beam_size=1`, `repetition_penalty=1.2`, `temperature=0.0`, `condition_on_previous_text=False`, `without_timestamps=True`, `vad_filter=False`
- [x] Định dạng input STT: raw PCM16 mono 16 kHz (record-service). Derivative `.ogg` Opus 32 kbps chỉ để nghe lại
- [x] Blacklist hallucination ban đầu: `HALLUCINATION_BLACKLIST` trong `whisper_marker_transcriber.py`
- [x] Server GPU (check 30/09): RTX 5090 32GB, driver 580.173 / CUDA 13.0, compute 12.0; 24 CPU; **RAM 30GB** (đọc audio lazy, `num_workers` ~6); ổ trống 340GB dùng chung với hệ thống; Python 3.12 + `uv` (không có pip3); ffmpeg 6.1 có libopus; tới được MinIO/PG `172.16.110.19` (9000/5432); không ai khác dùng GPU
- [x] Smoke test 1 qua (30/09): torch 2.11.0+cu128, ctranslate2 4.8.2 (hỗ trợ sm_120, có `float16`/`bfloat16`/`int8_float16`), faster-whisper 1.2.1 decode fp16 cả tuần tự lẫn batch. Cần `LD_LIBRARY_PATH` trỏ tới `nvidia/cublas/lib` + `nvidia/cudnn/lib` trong venv (lấy qua `__path__`, không phải `__file__`). `BatchedInferencePipeline` + `clip_timestamps`: 3 span → 3 segment, `seek = offset × 100`, start/end = span. Chưa kiểm tra trường hợp span không có output
- [x] Smoke test 2 + 3 qua (30/09, `scripts/00_smoke_test.py`): LoRA r=32 encoder+decoder = 27,9M tham số train (3,3%); batch 8 + gradient checkpointing: **1,63 s/step, peak VRAM 7,4GB** (feature extraction chạy trên main process, chưa có dataloader worker). Merge → HF → CT2 fp16 → faster-whisper: 128 mel đúng; output HF và CT2 giống hệt nhau. Span im lặng tuyệt đối **vẫn ra 1 segment** (3 span → 3 segment), nhưng code mapping vẫn phải coi seek bị thiếu là text rỗng. Turbo gốc với nhiễu bịa ra thêm câu *"Các bạn hãy đăng ký kênh để ủng hộ kênh của mình nhé."*, chưa có trong blacklist
- [x] Object key `.pcm` = `tracks.audio_info.filename`, nằm trong bucket `MINIO_BUCKET` (theo `transcription_service.py` và `whisper_transcription_processor.py`). Track xong STT có `status='completed'`. Track TTS của agent cũng có trong bảng `tracks`, cần loại bằng `audio_info.source`
- [ ] Giá trị `audio_info.source` của track mic (chạy `01_extract.py inspect` để xem)
- [ ] Codec và bitrate WebRTC từ client lên SFU (để chọn khoảng bitrate augment)
- [x] Biến môi trường (trùng tên với các service): `MINIO_ENDPOINT`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `MINIO_BUCKET`, `MINIO_SECURE`; `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DATABASE`
- [ ] User PostgreSQL read-only và credential MinIO cho script trích xuất (đặt trong `.env` trên server, không commit)
- [ ] Số giờ gán nhãn mỗi người mỗi ngày
- [ ] Danh sách thuật ngữ / tên riêng ban đầu cho `resources/terms.txt`
- [ ] Bổ sung câu hallucination từ grep `transcript_chunks`
- [ ] Xác nhận consent / chính sách dữ liệu khi dùng ghi âm cuộc họp để train
- [ ] Service production nào đang chạy (`Architect_MultiClient_Server/stt_service` hay `non_realtime_stt_service`), để port pipeline span vào đúng chỗ

---

## 14. Cấu trúc thư mục

```
mezon-whisper/
├── MEZON_WHISPER_FINETUNE_PLAN.md   # file này
├── configs/
│   ├── extract.yaml                 # PG/MinIO, số lượng track, kích thước cửa sổ
│   ├── span_pipeline.yaml           # tham số VAD + decode của pipeline span
│   ├── train_lora.yaml
│   └── train_full.yaml
├── labeling/
│   ├── guideline.md                 # hướng dẫn gán nhãn (mục 6)
│   ├── ls_clip.xml                  # template Label Studio cho clip
│   ├── ls_window.xml                # template Label Studio cho cửa sổ (region)
│   └── hallucination_blacklist.txt
├── resources/
│   └── terms.txt
├── requirements-gpu.txt             # torch cu128, faster-whisper 1.2.1, ctranslate2, ...
├── scripts/
│   ├── 00_smoke_test.py             # 3 bài test môi trường (mục 9.1)
│   ├── 00_download_public.py        # tải data công khai có giới hạn shard
│   ├── bench_rtf.py                 # RTF CPU int8 vs GPU fp16 (mục 9.1c)
│   ├── 01_extract.py
│   ├── 02_prelabel.py
│   ├── 03_labelstudio.py            # export / import
│   ├── 04_split.py
│   ├── 05_eval.py
│   ├── 06_prepare_public.py
│   ├── 07_train.py
│   └── 08_convert.sh
├── mezon_whisper/
│   ├── normalize.py                 # chuẩn hóa text cho WER (mục 7.1)
│   ├── vad.py                       # VAD của pipeline span (dùng chung train/eval/prod)
│   ├── span_transcriber.py          # pipeline span (mục 4)
│   ├── marker_adapter.py            # chạy pipeline marker hiện tại trên GPU (nhánh A)
│   ├── audio.py                     # đọc PCM16, augment Opus/nhiễu
│   └── manifest.py                  # đọc/ghi JSONL
├── tests/
│   ├── test_normalize.py
│   └── test_span_mapping.py
├── data/                            # KHÔNG commit (trừ manifests/split_rooms.json)
│   ├── raw/
│   ├── windows/
│   ├── clips/
│   ├── public/
│   └── manifests/
├── models/                          # KHÔNG commit
│   ├── runs/
│   ├── mezon-whisper-hf/
│   └── mezon-whisper-ct2/
└── reports/
```

---

## 15. Backlog cho v2 (ngoài phạm vi 2 tuần)

- Vòng lặp active learning hàng tháng từ các segment có độ tin cậy thấp ở production.
- Tăng dữ liệu in-domain có nhãn người lên 50–100 giờ, sau đó thử full finetune.
- Shadow mode tự động, dùng cờ `model_version` trên `transcription:stream`.
- Port pipeline span + model vào STT service sau feature flag, kèm `model_version`/`pipeline` metadata và đường rollback. Gỡ Gipformer.
- So sánh VAD thay thế (pyannote segmentation-3.0, TEN VAD) với Silero.
- Forced alignment (wav2vec2 CTC tiếng Việt) nếu sau này cần timestamp mịn hơn mức span.
- Đánh giá chất lượng summary định kỳ, dùng LLM làm giám khảo kèm người kiểm tra lại.
