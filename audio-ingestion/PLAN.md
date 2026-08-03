# Audio Ingestion — Kiến trúc & Kế hoạch triển khai

> Tài liệu sống. Cập nhật mục "Trạng thái triển khai" khi làm xong từng phần — không xoá phần lý do/quyết định cũ, chỉ đánh dấu trạng thái, để không mất context giữa các phiên làm việc.

## 1. Bối cảnh & mục tiêu

Hệ thống đang dùng **LiveKit Egress** để ghi âm cuộc gọi, khiến hệ thống phụ thuộc nặng vào LiveKit. Mục tiêu: tách dần khỏi LiveKit, bắt đầu bằng việc **tự chủ khâu ghi âm**.

Bối cảnh liên quan:
- Team hạ tầng LiveKit (team khác) đã bỏ Redis khỏi cụm LiveKit server, và dự kiến vài tuần tới sẽ bỏ hẳn LiveKit, chuyển sang meeting/SFU provider khác.
- Vì vậy: không dùng feature flag / giải pháp tạm — làm sạch luôn, không giữ lại bất kỳ hình dạng nào của "egress" (kể cả webhook event shape).
- Ràng buộc nghiệp vụ: **chỉ ghi âm cho room nào đã có agent (LiveKit Agent worker) tham gia**. Không có agent → không ghi. Agent "gãy" → dừng ghi là hành vi chấp nhận được (tiết kiệm resource, không phải bug).
- Audio lưu trên MinIO sẽ **được expose trực tiếp cho client nghe lại** (không chỉ phục vụ nội bộ cho STT).

## 2. Kiến trúc tổng thể

Domain "âm thanh của 1 cuộc gọi" được gom vào folder này, gồm **2 service độc lập, deploy/scale riêng**, không gộp chung 1 deployable vì khác hồ sơ vận hành (1 bên phải cực kỳ đáng tin cậy và tối giản, 1 bên CPU-heavy và sẽ tiến hoá liên tục):

```
audio-ingestion/
├── record-service/            # Capture âm thanh thô — CRITICAL PATH, tối giản tối đa
└── audio-processing-service/  # Transcode ra bản nghe được cho client — non-critical, async, CPU-bound
```

Ngôn ngữ triển khai cho cả 2 service: **Python + `grpc.aio`** — tái dùng gần nguyên logic sẵn có ở `agents/src/services/audio_recording_manager.py` (multipart upload, retry) thay vì viết lại từ đầu.

Luồng dữ liệu tổng quát:

```
agents (LiveKit Agent worker)
   │  subscribe track riêng cho recording (Phase 2, độc lập STT realtime) → forward PCM qua gRPC
   ▼
record-service
   │  ghi raw PCM thẳng lên MinIO (multipart upload)
   │  báo recording.started / recording.completed / recording.failed → orchestrator (HTTP, có retry)
   ▼
orchestrator
   │  recording.started  → tạo Track row (thay cho egress_started webhook cũ)
   │  recording.completed → Track.status="wait_process" + publish transcription:stream (STT Whisper,
   │                         KHÔNG đổi gì downstream) + publish audio_derivative:stream (mới)
   ▼
audio-processing-service
   │  consume audio_derivative:stream (Redis Stream, không phải Outbox — xem D17)
   │  transcode raw PCM → OGG/Opus (giữ nguyên định dạng cũ, xem D20)
   │  upload bản derivative lên MinIO, báo derivative.completed/.failed → orchestrator
   ▼
orchestrator
   │  cập nhật Track.derivative_status; khi CẢ room finalize LẪN mọi track trong room có
   │  derivative_status ở trạng thái cuối → bắn room_record_done (SSE metadata, notice trần,
   │  không kèm path — xem D19)
   ▼
Bot / FE client (đã có quyền) → tự gọi API riêng (đã có sẵn, ngoài phạm vi) để lấy path thật
```

## 3. `record-service` — thiết kế chi tiết

### Nguồn audio
Agent forward raw PCM frame sang record-service qua gRPC streaming — **không** để record-service tự join LiveKit room. Lý do (xem Decision Log D3).

### Kiến trúc nội bộ: Ports & Adapters (Hexagonal)
Để sau này swap "nhận frame từ agent" → "tự đọc RTP trực tiếp từ SFU mới" mà không đổi logic lõi:

```
record-service/
  domain/            # thuần business logic, zero dependency ra ngoài
    models.py         # RecordingSession, AudioChunk, RecordingStatus
    ports.py           # AudioSource, BlobStorage, SessionStateRepository, EventReporter (interface)
    policies.py         # grace-period, retry policy — quy tắc nghiệp vụ
  application/         # use case, điều phối domain qua port
    start_recording.py
    append_audio.py
    stop_recording.py
    recover_orphaned_sessions.py
    report_event.py
  infra/               # toàn bộ "thế giới bên ngoài"
    grpc/               # adapter hôm nay — implement AudioSource qua gRPC stream từ agent
      ingest_server.py
      recording.proto
    sfu/                # (tương lai, chưa build) — sẽ implement AudioSource đọc RTP trực tiếp
      rtp_audio_source.py
    storage/
      s3_blob_storage.py       # implement BlobStorage (boto3, multipart upload)
    state/
      file_session_state_repo.py  # implement SessionStateRepository (phục vụ recovery)
    reporting/
      http_event_reporter.py    # implement EventReporter, retry+backoff
  bootstrap.py          # wiring: chọn adapter, dựng use case, start server
```

### Format lưu trữ (critical path)
**Raw PCM thô, không header, không encode** (`.pcm`). Lý do: giảm tối đa bề mặt lỗi trên đường găng (không subprocess ffmpeg, không state `encoder_failed`, không cần biết trước tổng size để ghi header). Metadata (`sample_rate`, `channels`, `bit_depth`) lưu ở session state / DB, không nhúng vào file. Sample rate dùng chung 16kHz mono — đúng luồng PCM đã có sẵn cho STT, không mở thêm subscription riêng.

### Cơ chế phục hồi lỗi — 3 tầng
Phân biệt rõ: **lỗi upstream (agent) → dừng ngay**, hợp lý, không cần cơ chế gì thêm. **Lỗi downstream (MinIO / worker / network) → 3 tầng**:

1. **Retry tại chỗ**: backoff cho `create_multipart_upload` / `upload_part` / `complete_multipart_upload` (lỗi MinIO thoáng qua).
2. **Grace-period + resumable session**: khi stream agent↔record-service đứt *bất thường* (network blip, record-service process còn sống) — session vào `GRACE_WAIT` (~30-60s), agent reconnect với cùng session key (`room_id + track_id`) thì nối tiếp; hết grace period thì finalize best-effort. Nếu agent *chủ động* đóng stream (track unpublish / shutdown bình thường) → finalize ngay, không chờ.
3. **Local durable state + reconciliation on startup**: tận dụng tính chất **S3 multipart upload tự nó đã resumable** — chỉ cần persist `(upload_id, bucket, key, parts_uploaded)` mỗi khi 1 part upload xong, không cần persist lại audio bytes đã upload thành công. Khi worker crash & restart, quét state store, finalize các session mồ côi bằng phần đã có. Rủi ro mất dữ liệu chỉ giới hạn ở phần buffer chưa flush (tối đa 1 `part_size`).

Báo ngược cho agent: chỉ khi record-service từ chối/đóng stream (không tạo nổi multipart upload lúc mở, hoặc spool vượt ngưỡng) thì agent mới coi là "ghi âm tạm không khả dụng" và tự backoff mở lại — **tuyệt đối không để việc này chặn pipeline STT/hội thoại đang chạy song song** (dùng queue riêng + task riêng phía agent, best-effort drop nếu cần).

### Báo cáo sự kiện về orchestrator
Gọi HTTP trực tiếp tới endpoint mới (không giả dạng webhook LiveKit như code cũ). Bọc đúng recipe recovery ở trên — dùng chung state file của session (`reported: false/true`), không xây cơ chế song song. Endpoint orchestrator **bắt buộc idempotent** (upsert theo `recording_id`) vì retry chắc chắn sinh trùng lặp.

### Ghi chú triển khai thực tế (Phase 1, so với sketch ban đầu)

Code thật ở `record-service/` (Python + `grpc.aio`, đã chạy `pytest` xanh 12/12 test, generate proto thành công). Vài điểm lệch nhỏ so với sketch ban đầu trong tài liệu này, ghi lại để không lệch context:

- **Bỏ port `AudioSource` độc lập.** Thay vào đó, chính 3 use case `StartRecording`/`AppendAudio`/`StopRecording` (`application/`) đóng vai trò inbound port — adapter nào (gRPC hôm nay, `infra/sfu/` sau này) cũng chỉ cần gọi thẳng 3 hàm này theo đúng thứ tự. Đơn giản hơn sketch cũ, đạt cùng mục tiêu seam ở D4.
- **gRPC đổi từ client-streaming (1 response cuối) sang bidi-streaming** (`rpc StreamAudio(stream AudioChunk) returns (stream RecordingAck)`). Bắt buộc phải vậy để record-service có thể accept/reject session **ngay khi nhận `SessionStart`** (D5 tier 1 — "báo ngược cho agent") thay vì phải đợi agent gửi xong cả cuộc gọi mới biết bị từ chối.
- **D11/D12 hiện thực bằng `QualityAnnotation` gắn vào session** (`reason="low_byte_rate"` / `"high_drop_rate"`), đi thẳng vào payload báo cáo orchestrator — không chỉ là log line, đúng tinh thần "quan sát được" đã thảo luận.
- Thêm `application/session_registry.py` (buffer + lock + grace-timer per session — orchestration state, không phải domain state), `application/retry.py`, `application/finalize.py` (complete/abort dùng chung giữa `stop_recording.py` và `recover_orphaned_sessions.py`) — không có trong sketch thư mục ban đầu nhưng cần thiết để tránh trùng lặp logic.
- `infra/naming.py`: record-service tự sinh `object_key`, không hỏi orchestrator — giữ đúng nguyên tắc "bắt đầu ghi không phụ thuộc đồng bộ vào orchestrator".
- **Chưa làm trong Phase 1** (đúng kế hoạch): `infra/sfu/rtp_audio_source.py` (để dành Phase 6), tích hợp thật với MinIO/orchestrator đang chạy (cần Phase 2/3), build Docker image thật (Dockerfile đã viết, dùng đúng lệnh gen proto đã verify local, nhưng chưa `docker build` trong môi trường này).

### Triển khai: Docker (local) vs systemd (dev/prod)

Xem D13. `Dockerfile` hiện có vẫn giữ nguyên, chỉ dùng cho local dev loop (docker-compose) và build image cho CI test — **không dùng để chạy ở dev/prod**. Ở dev/prod, service chạy trực tiếp trên host bằng systemd, không qua container runtime.

Gợi ý cấu trúc (minh hoạ, chưa tạo file thật — để dành phần đóng gói triển khai, xem Phase 4 mở rộng):

```
[Unit]
Description=record-service (audio-ingestion) - instance %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=record-service
Group=record-service
EnvironmentFile=/etc/record-service/record-service.env
Environment=RECORD_SERVICE_GRPC_PORT=%i
ExecStart=/opt/record-service/.venv/bin/python -m record_service.main
Restart=on-failure
RestartSec=2
StateDirectory=record-service
CPUQuota=100%
MemoryMax=512M

[Install]
WantedBy=multi-user.target
```

Dùng **template unit** (`record-service@.service`, chạy nhiều instance `record-service@50051`, `record-service@50052`...) để tận dụng nhiều core — xem D13 vì đây không phải tối ưu tuỳ chọn mà là bắt buộc do giới hạn GIL của 1 process Python.

### CPU/resource profile (D13)

So với LiveKit Egress (~4-6%/core cho 1 recording, chủ yếu do pipeline GStreamer decode/remux), cách implement hiện tại **về lý thuyết nhẹ hơn đáng kể trên mỗi session** — vì D6 đã loại bỏ hẳn bước encode/decode, phần việc còn lại của record-service (nhận gRPC message, `bytearray.extend`, upload part qua `asyncio.to_thread`, ghi state JSON) là I/O-bound, không có codec nào chạy cả.

Nhưng có 1 giới hạn khác cần lưu ý, khác hẳn cách Egress scale: **record-service hiện là 1 process Python duy nhất** (`main.py` chạy `asyncio.run(serve())`), nên toàn bộ phần code Python thực thi (không phải lúc đang chờ I/O) bị GIL giới hạn trong ~1 core, bất kể mỗi session rẻ tới đâu. Tức là mô hình chi phí không phải "N session × %CPU/session" giống Egress (vốn có thể chạy nhiều pipeline song song trên nhiều core), mà là "1 core's worth of Python execution chia sẻ giữa tất cả session đang chạy trong process đó". Số session tối đa 1 process chịu được là con số cần **đo thật**, không nên đoán — xem action item bên dưới.

Hướng scale đúng: chạy **nhiều process** (không phải nhiều thread) — khớp tự nhiên với template unit systemd ở trên. Nhưng việc này kéo theo 1 yêu cầu thiết kế chưa giải quyết: D5 tier 2 (grace-period reconnect) giả định agent reconnect lại **đúng process đang giữ session đó trong bộ nhớ** — nếu có N process độc lập, cần 1 cơ chế routing nhất quán theo `session_id` (ví dụ agent tự hash `room_id:track_id` để chọn port cố định, hoặc 1 reverse proxy consistent-hashing phía trước N process) trước khi tách nhiều instance. Chưa cần giải quyết ngay ở Phase 1/2, nhưng phải nhớ trước khi triển khai multi-instance thật ở dev/prod.



## 4. `audio-processing-service` — thiết kế chi tiết

- Nghe `audio_derivative:stream` (Redis Stream + consumer group, KHÔNG phải Outbox — xem D17) do orchestrator publish ngay khi nhận `recording.completed`.
- Transcode raw PCM → OGG/Opus, **giữ nguyên định dạng đang dùng hiện tại** (đã có sẵn từ thời LiveKit Egress, client/bot đã tích hợp theo định dạng này) — xem D20. Không đổi sang MP4/M4A dù về mặt kỹ thuật Safari không phát được audio trong container OGG — chấp nhận giới hạn có sẵn từ trước, không phải rủi ro mới phát sinh.
- **Số bản/chất lượng v1**: chỉ 1 bản duy nhất, bitrate cân bằng giữa dung lượng và chất lượng nghe (không làm multi-bitrate/multi-rendition ở v1). Nhiều bản chất lượng là tối ưu để sau, chưa cần thiết.
- Không critical — fail thì retry, không đe doạ bản gốc (đã an toàn trên MinIO từ `record-service`).
- Scale độc lập với `record-service` (CPU-bound, khác hồ sơ tải).
- Báo `derivative.completed`/`derivative.failed` về orchestrator sau khi xong — orchestrator dùng để cập nhật `Track.derivative_status` và xét điều kiện bắn `room_record_done` (xem D19).

### Ghi chú triển khai thực tế (Phase 5)

Code thật ở `audio-processing-service/` (Python + Redis Stream consumer, không dùng Ports & Adapters như record-service — xem README.md của service này để biết lý do). 12/12 test pass, bao gồm test chạy `ffmpeg` thật (không mock) xác nhận command line transcode tạo ra file Opus hợp lệ (`ffprobe` parse được) từ raw PCM headerless.

Vài điểm đáng chú ý so với thiết kế ở mục 4 phía trên:

- **Tái dùng nguyên `RedisStreamService`/connection pool từ `stt_service`** (bản copy thứ 3, sau `orchestrator_service`) — theo đúng quyết định D28 điểm 3, giữ nguyên cả 2 bug nhẹ đã phát hiện lúc review (xem D28 để biết chi tiết + lý do không sửa).
- **Báo `derivative.failed` chỉ ở lần thử cuối cùng** (`task.retry_count >= max_retries`, tức lần thử sẽ bị đẩy sang DLQ) — không báo ở các lần thử sớm hơn. Lý do: nếu báo failed ngay từ lần thử đầu rồi track được retry thành công sau đó, `check_and_notify_room_recordings_ready` (D19) có thể đã coi track "xong" (failed = terminal) và bắn `room_record_done` sớm trước khi bản derivative thật sự sẵn sàng. Chi tiết + test chứng minh: `services/derivative_processor.py`, `tests/test_derivative_processor.py`.
- **File-based, không streaming qua ffmpeg pipe** như bản cũ của agent (`pipe:0`/`pipe:1`) — tải nguyên file PCM về temp dir, transcode file→file, upload nguyên file kết quả. Đơn giản hơn, chấp nhận được vì không phải critical path (D7) và dung lượng PCM 16kHz mono tối đa vài trăm MB cho 1 cuộc gọi rất dài.
- **`ffmpeg` là dependency hệ điều hành, không phải Python package** — cần cài qua `apt-get install ffmpeg` trên host deploy (đã ghi trong `deploy/systemd/README.md`), không nằm trong `pyproject.toml`.
- **Không cần cơ chế load-balancing/routing như record-service** — mọi instance chỉ là 1 consumer trong cùng 1 consumer group Redis (`audio-processing-workers`), Redis tự chia việc; không có gRPC port riêng từng instance như record-service nên không phát sinh câu hỏi "agent nối vào instance nào".

## 5. Thay đổi ở các service hiện có

| Service | Thay đổi |
|---|---|
| `agents` | ✅ Phase 2: thêm nhánh forward PCM sang `record-service` qua gRPC. ✅ Phase 3: xoá hẳn `AudioRecordingManager`/`_manage_track_recording`/`_handle_egress_start/end`/`start_audio_recording`/`has_track`, `EgressInfo`/`EgressWebhook`/`FileConfig`/`FileResult`/`FileInfo`/`TrackInfo`/`push_webhook` trong `orchestrator_client.py`, `AgentRequestType.START_AUDIO_RECORDING` cả 2 phía. |
| `orchestrator` | ✅ Phase 3: endpoint idempotent `POST /api/v2/recordings/events` nhận `recording.completed/failed` từ record-service và `derivative.completed/failed` từ audio-processing-service (D22: không có `recording.started` riêng). `audio_derivative:stream` (Redis Stream, D17). Cột `Track.derivative_status`, `Room.record_notified_at`. Hàm `check_and_notify_room_recordings_ready()` (D19). Xoá `egress_service.py`, `services/redis/egress_repository.py`, `utils/filepath.py`, các handler egress trong `webhook_handler.py`/`webhook_api.py`/`room_registry_api.py` (v1+v2), `EgressInfo` trong `webhook_models.py`. |
| docker-compose (dev/prod) | Chưa làm — để dành Phase 4 (không có docker-compose ở local để test, xem ghi chú Phase 0). |
| `orchestrator` (Phase 5) | ✅ `get_audio_info()` (`services/room_service.py`) map field `filename` sang `audio_info.derivative_object_key`, giữ nguyên field name/shape — không đổi gì phía downstream/client (D28 điểm 2). |
| Postgres | **Không đổi ngay** `tracks.egress_id` (PK) — migration riêng sau (D10). Đã thêm: `Track.derivative_status`, `Room.record_notified_at` (migration `005_add_derivative_tracking`, đã chạy thật trên Postgres thật để verify). |

### Ghi chú triển khai thực tế (Phase 2)

Code thật ở `agents/src/services/record_service_client.py` (`RecordForwarder` + `RecordServiceClient`). Đã verify **interop thật** (không chỉ mock): dựng `record-service` thật (venv riêng, wired fakes qua `record-service/scripts/dev_server_with_fakes.py`) + client thật từ `agents` (venv riêng, `agents/scripts/dev_forward_smoke_test.py`) nói chuyện qua gRPC thật trên localhost — xác nhận 2 bộ stub sinh độc lập từ 2 bản copy `recording.proto` (agents và record-service) tương thích nhau trên wire, byte count khớp, và sanity-check D11 tự động bắt được test data cố tình ngắn (`low_byte_rate`) — chứng minh cả pipeline lẫn cơ chế quan sát đều chạy đúng end-to-end.

**Sửa sau review với người dùng**: bản đầu wire forwarding vào `manage_speaker_transcription` (dùng chung subscription với STT) — **sai**, vì đó là luồng STT realtime phục vụ 1 business logic cụ thể, có toggle bật/tắt (`AgentControlState`), không phải lúc nào cũng chạy. Recording phải theo **vòng đời agent/track** (phục vụ luồng STT non-realtime qua Whisper riêng ở `stt_service`), độc lập với toggle STT realtime. Đã sửa lại: bỏ hoàn toàn code forward khỏi `manage_speaker_transcription`, tạo 2 method mới `_start_record_forwarding`/`_forward_track_to_record_service` với **subscription `rtc.AudioStream.from_track` độc lập riêng**, khởi động ngay trong `on_track_subscribed` (không chờ `transcription_enabled`), dọn dẹp ở `on_track_unsubscribed`/`on_participant_disconnected`/`safe_disconnect_all` — cùng nhịp với `recording_tasks` (bản ffmpeg cũ) chứ không phải với `transcription_tasks`. Đánh đổi: chấp nhận có thể có tới 3 subscription cùng lúc trên 1 track khi cả STT realtime + recording + fallback egress cũ đều đang chạy — chấp nhận được vì audio 16kHz mono rẻ (đã đánh giá ở D13), và fallback egress cũ sẽ bị xoá dần.

Các điểm khác từ lần review trước vẫn giữ nguyên:

- **`room_id` trong `SessionStart` = `ctx.room.name`** (tên/slug phòng), **không phải** room_id dạng UUID mà orchestrator dùng nội bộ (`registry.get_room_id(room_name)`). record-service chỉ coi đây là chuỗi cơ hội để đặt tên object key — việc quy đổi sang room UUID nội bộ là trách nhiệm của orchestrator khi nhận event ở Phase 3, giống hệt cách `webhook_handler.py` cũ đang làm với `roomName` từ LiveKit webhook.
- ~~`RECORD_SERVICE_ENABLED` mặc định = `0`~~ — **đã bỏ hẳn ở D24/D25** sau khi Egress bị xoá hoàn toàn (Phase 3): không còn gì để flag này "rollback" về nữa, giữ lại chỉ tạo rủi ro ngừng ghi âm âm thầm nếu quên bật. Forwarding giờ luôn được thử; `RecordServiceClient.new_forwarder` tự fail-soft theo từng track nếu record-service không reachable.
- Proto duplicate có chủ đích giữa `agents/src/proto/recording.proto` và `record-service/proto/recording.proto`, mỗi bên tự generate qua `scripts/gen_proto.sh` riêng — đã note rõ trong cả 2 file proto để nhắc đồng bộ tay khi sửa.

### Ghi chú triển khai thực tế (Phase 3)

**Verify end-to-end thật** (không chỉ mock): dựng Postgres + Redis thật qua docker (container tạm, đã xoá sau khi xong), chạy migration `005_add_derivative_tracking` thật trên schema thật, chạy `FastAPI` app thật (`httpx.AsyncClient` + `ASGITransport`, không qua mock). Kịch bản test: đăng ký room → track-1 + track-2 báo `recording.completed` (kiểm tra idempotent khi gửi lại) → track-1 báo `derivative.completed` (xác nhận `room_record_done` **chưa** bắn vì room chưa finalize và track-2 chưa xong) → gọi `final_room()` (xác nhận **vẫn chưa** bắn vì track-2 còn pending) → track-2 báo `derivative.completed` (xác nhận **bắn đúng lúc này**, và `check_and_notify_room_recordings_ready` không bắn lần 2 khi gọi lại) → kiểm tra trực tiếp trong Postgres payload event đã lưu là `metadata: {}` (bare notice thật, không lộ file info). Tất cả pass. Đây là bằng chứng thật cho D19 hoạt động đúng như thiết kế, không chỉ đúng trên giấy.

Cũng phát hiện qua test thật: `RecordingEventService` cần `get_room_registry().get_room_id()` — tức là room phải được đăng ký qua `/room-registry/register` (hoặc gọi thẳng `registry.register_room()`) trước khi record-service có thể báo event cho room đó, giống hệt yêu cầu cũ với webhook LiveKit. Không phải thay đổi gì, chỉ là xác nhận lại 1 ràng buộc đã có từ trước vẫn đúng.

Vài quyết định/sửa lỗi phát sinh trong lúc code, xem chi tiết ở D21–D23.

## 6. Decision log (quyết định & lý do)

- **D1** — Bỏ LiveKit Egress, dùng cơ chế agent-side capture (đang là fallback) làm đường chính. *Vì*: Egress là service nặng (GStreamer/Chrome), không cần thiết khi đã có sẵn pipeline subscribe PCM.
- **D2** — Không dùng feature flag, làm sạch hoàn toàn, bỏ luôn hình dạng "egress" (kể cả webhook event giả lập). *Vì*: hướng bỏ LiveKit đã chắc chắn, không cần đường lùi.
- **D3** — Audio source = agent forward frame sang `record-service`, KHÔNG để `record-service` tự join room. *Vì*: nếu record-service tự join thì lại thêm 1 điểm phụ thuộc LiveKit (phải sửa 2 chỗ khi đổi provider); agent vốn đã cần PCM cho STT bất kể provider nào; vòng đời record gắn vòng đời agent là đúng theo luật nghiệp vụ (chỉ record room có agent).
- **D4** — `record-service` thiết kế Ports & Adapters, port `AudioSource` là seam để sau này thêm `SfuRtpAudioSource` khi đổi SFU, không đổi domain/application layer.
- **D5** — Recovery 3 tầng dựa trên tính chất resumable của S3 multipart upload — chỉ cần persist `upload_id` + `parts`, không cần persist lại bytes đã upload thành công.
- **D6** — Format capture = raw PCM headerless, không WAV, không Opus ở critical path. *Vì*: tối giản hot path, loại bỏ hẳn 1 lớp thất bại (subprocess encoder).
- **D7** — Bản phát cho client là 1 job async tách riêng ở `audio-processing-service`, không chạy trên critical path, không xử lý ở client (chi phí băng thông lặp lại mỗi lần nghe, mất UX `<audio>` native, trùng lặp logic ở mọi platform client).
- **D8** — Không đặt job transcode trong `orchestrator` (sai hồ sơ vận hành — CPU-bound vs I/O-bound/coordination).
- **D9** — 2 service (`record-service`, `audio-processing-service`) tách deploy riêng nhưng gom chung domain folder `audio-ingestion/` — vừa giữ ranh giới vận hành, vừa giữ ranh giới nghiệp vụ rõ ràng. Theo đúng convention repo đã có (`stt_service`, `tts_service` cũng tách riêng).
- **D10** — PK `tracks.egress_id` và các tên gọi liên quan "egress" trong Postgres: để migration riêng sau, không gộp vào đợt dọn dẹp này (rủi ro đổi PK trên bảng đang có dữ liệu).
- **D11** — Raw PCM không header là đánh đổi có rủi ro riêng: corruption *ngữ nghĩa* (metadata sai lệch, frame bị drop âm thầm, đoạn im lặng/rỗng) khiến upload "thành công" nhưng nội dung không dùng được — khác hẳn loại lỗi *pipeline* mà D6 đã né. Không quay lại encode để né rủi ro này (không giải quyết đúng vấn đề, chỉ tốn lại đúng thứ D6 vừa bỏ). Thay vào đó thêm sanity-check rẻ, không cần decode:
  - So `total_bytes` thực tế với kỳ vọng (`elapsed_time × sample_rate × channels × 2 byte`) lúc `stop_recording` — lệch nhiều thì gắn `quality_warning`, không âm thầm coi là `completed` sạch.
  - Đếm số frame bị drop (do chính sách backpressure bảo vệ STT) và đưa vào metadata report — biến mất-dữ-liệu-âm-thầm thành quan sát được.
  - Tận dụng chính job transcode ở `audio-processing-service` (D7) làm điểm verify tự nhiên: so `duration` bản derivative với `ended_at - started_at` của session, lệch nhiều thì đánh dấu `needs_review` thay vì coi là xong. Không cần thêm component mới, chỉ thêm 1 assertion vào bước đã có kế hoạch.
  - Với ghi âm real-time, "retry" theo nghĩa thu lại là bất khả thi — giá trị của phát hiện sớm là *báo trung thực và kịp thời*, không phải để retry capture.
- **D12** — Khi rolling check (ví dụ tỉ lệ drop-frame cao kéo dài) phát hiện chất lượng kém *ngay trong lúc đang ghi*: `record-service` **không được tự ý bỏ đoạn dữ liệu và tạo session mới** — vì đó là hành động không thể đảo ngược dựa trên 1 heuristic có thể sai (false positive), đi ngược nguyên tắc "mất file là mất tất cả". Thay vào đó:
  - `record-service` vẫn ghi liên tục toàn bộ như bình thường, chỉ phát sinh **annotation** ("khoảng [t1,t2] nghi ngờ chất lượng kém") gắn vào metadata session — không hành động, không phán xét.
  - Quyết định "có cắt bỏ đoạn đầu kém chất lượng khi tạo bản nghe cho client hay không" đẩy xuống `audio-processing-service` — nơi vốn đã async, đã chấp nhận retry, đã là chỗ đưa ra phán đoán "làm sao cho ra bản trình bày được". Sai ở đây thì làm lại rẻ; sai ở `record-service` thì mất vĩnh viễn.
  - Nguyên tắc chung: **record-service không bao giờ được làm gì không thể đảo ngược**.
  - Khái niệm ">1 recording session cho 1 track" không phải phát minh mới — đã tồn tại như edge case hiếm từ D5 (grace-period hết hạn rồi agent mới reconnect). Chưa chủ động thêm trigger mới (dựa trên tín hiệu chất lượng) để tạo session mới ở v1 — để dừng ở mức annotation, xem dữ liệu thực tế (từ D11) rồi mới quyết có đáng làm tiếp ở v2 hay không.
- **D13** — CPU/resource: v1 không encode (D6) nên về lý thuyết nhẹ hơn LiveKit Egress (~4-6%/core mỗi recording, chủ yếu do GStreamer decode/remux) trên mỗi session — phần việc còn lại (gRPC, buffer copy, upload qua `asyncio.to_thread`, ghi state JSON) là I/O-bound. Nhưng record-service hiện là **1 process Python duy nhất**, nên phần thực thi Python (ngoài lúc chờ I/O) bị giới hạn bởi GIL trong ~1 core — mô hình chi phí là "1 core chia sẻ giữa mọi session trong process", khác hẳn Egress (nhiều pipeline chạy song song nhiều core). *Chưa có số đo thật* — cần benchmark bằng chính skeleton Phase 1 (giả lập N stream đồng thời, đo CPU% theo concurrency) trước khi chốt số process/core cần cho dev/prod, không nên đoán suông. *Action item*: benchmark trước khi lên kế hoạch capacity dev/prod. **Tooling đã có** (`scripts/benchmark_concurrency.py`, Phase 4) — mở nhiều channel gRPC giả lập nhiều agent worker (không dồn hết vào 1 channel — không đúng traffic pattern thật), bơm PCM16 silence theo đúng nhịp capture thật (16kHz mono, frame 20ms), sweep concurrency, đo CPU%/RSS process target + tự cảnh báo nếu chính script generator trở thành nghẽn. Chạy chung 1 máy vẫn ổn (không bắt buộc máy riêng như bàn đầu) — dùng `taskset -c` pin record-service và generator vào 2 core khác nhau. *Vẫn chưa có số đo thật* — cần người dùng tự chạy trên dev/prod thật để chốt N instance.
- **D14** — Triển khai dev/prod dùng **systemd trực tiếp trên host, không dùng Docker** (Docker chỉ giữ cho local dev loop + CI). *Vì*: techlead lo Docker runtime ảnh hưởng resource server; systemd + cgroup (`CPUQuota`, `MemoryMax`) cho đúng khả năng giới hạn tài nguyên như Docker mà không thêm lớp container runtime. Dùng **systemd template unit** (nhiều instance, nhiều port) làm cách scale nhiều core — hệ quả trực tiếp của D13 (1 process = ~1 core). Lợi ích phụ: state directory (D5 tier 3) nằm trên đĩa host thật, không còn rủi ro "pod bị reschedule sang node khác mất state" đã nêu ở D5 (rủi ro đó chỉ áp dụng nếu sau này chuyển sang k8s).
- **D15** — **Commit thẳng file generated từ proto** (`recording_pb2.py`/`recording_pb2_grpc.py`, cả bên `agents` lẫn `record-service`), không gitignore. *Vì*: convention "đừng commit generated code" giả định có CI tự regenerate + diff để bắt lệch — dự án chưa có; và quan trọng hơn, D14 đã chọn deploy bằng systemd trên host trần, tức môi trường chạy thật không có bước build nào cả — nếu không commit, mỗi lần deploy phải nhớ chạy `gen_proto.sh` bằng tay, quên 1 lần là service crash vì thiếu file. Coi các file này như 1 dạng contract/DTO đã build sẵn, không phải "code tự sinh cần né". Hệ quả kéo theo: `grpcio-tools` (chỉ cần lúc *sửa* proto) chuyển xuống `dev` extras, không còn là runtime dependency; phát hiện luôn 1 thiếu sót khi soát lại — `protobuf` (runtime cho chính file `_pb2.py`) trước đó ăn theo `grpcio-tools` kéo vào chứ chưa khai báo tường minh, đã thêm vào `dependencies` của `record-service`. Dockerfile không còn chạy `protoc` lúc build nữa, chỉ copy file đã commit. *Quy trình khi sửa proto*: chạy `scripts/gen_proto.sh` bằng tay, commit `.proto` + file generated trong cùng 1 commit.

- **D16** — Soát race condition trong `record-service` (theo yêu cầu review trước khi sang Phase 3), tìm và sửa 3 lỗi thật:
  1. **`RecoverOrphanedSessions` có thể finalize nhầm session đang sống** — reconciler định kỳ chỉ đọc state file trên đĩa, không biết gì về `SessionRegistry` trong RAM, nên coi 1 session đang `RECORDING` hợp lệ (state được `AppendAudio` lưu sau mỗi part) là "mồ côi do crash" và complete upload của nó — cắt cụt bản ghi đang chạy thật. *Nghiêm trọng nhất trong 3 lỗi.* Sửa: `RecoverOrphanedSessions` nhận thêm `SessionRegistry`, bỏ qua mọi session còn sống trong registry của chính process đó.
  2. **Race giữa resume-trong-grace-period (`StartRecording`) và hết-hạn-grace-period (`StopRecording._grace_timeout`)** — cả 2 đọc/ghi `session.status` không qua khoá nào, agent reconnect đúng lúc timer 45s hết hạn có thể khiến: hồi sinh 1 session đã finalize, hoặc cắt cụt 1 session vừa resume xong. Sửa: cả 2 phía cùng giành quyền quyết định qua `active.lock` (per-session) — bên nào acquire trước thắng, bên thua phát hiện trạng thái đã đổi và tự rút lui (resume thất bại thì tạo session mới, finalize thấy đã bị resume thì bỏ qua).
  3. **`StartRecording.execute` tự nó có race tạo trùng** — `registry.get()` không khoá, giữa lúc đó có `await create_multipart_upload`, 2 lời gọi đồng thời cho cùng track có thể cùng thấy "chưa tồn tại", tạo 2 multipart upload riêng, `registry.put()` sau đè lên cái trước — 1 upload bị rò rỉ vĩnh viễn không ai complete/abort. Sửa: thêm `asyncio.Lock` bọc toàn bộ quyết định "đã tồn tại hay tạo mới" trong `StartRecording` (chấp nhận serialize các lượt start vì đây không phải hot path).

  Đã thêm 2 test chứng minh trực tiếp 2 fix quan trọng nhất (`test_reconciler_never_touches_a_session_live_in_this_process`, `test_late_reconnect_after_grace_timeout_claimed_the_session_gets_a_fresh_upload`) — 14/14 test pass.

  **Phát hiện liên quan, chưa sửa (không phải race, là thiếu tính năng)**: `RecordForwarder` bên `agents` hiện **không có logic tự reconnect** khi kết nối gRPC tới record-service chết giữa chừng — writer/reader loop chỉ log lỗi rồi dừng, `send_audio()` sau đó cứ đẩy vào queue đầy rồi drop mãi mãi, không bao giờ tự mở lại stream. Nghĩa là cơ chế grace-period/resume ở D5 tier 2 hiện **không có gì kích hoạt nó từ phía client thật** — chỉ mới đúng về mặt logic (đã test), chưa có đường để thực sự xảy ra trong vận hành thật.

  *Quyết định*: **chưa làm ngay**. *Vì*: trong ngắn hạn `agents` và `record-service` chạy trên cùng 1 server vật lý — network blip giữa 2 bên gần như không đáng kể (khác hẳn kịch bản qua network thật giữa các host riêng mà D5 tier 2 vốn nhắm tới). *Cần nhớ lại*: nếu sau này 2 service tách host/tách server (kể cả khi vẫn cùng datacenter), hoặc quan sát thực tế thấy disconnect xảy ra, phải quay lại làm reconnect logic cho `RecordForwarder` thì D5 tier 2 mới thực sự phát huy tác dụng — hiện tại nó chỉ đúng về logic (có test), chưa được khai thác.

- **D17** — Dispatch `orchestrator` → `audio-processing-service` dùng **Redis Stream + consumer group** (`audio_derivative:stream`), KHÔNG dùng bảng `OutboxTask`/`SummaryOutboxWorker` như dự tính ban đầu ở section 4/5. *Vì*: soát kỹ code `SummaryOutboxWorker` thấy nó không hợp: chỉ chạy đúng 3 khung giờ cố định trong ngày (19h/20h/21h, không phải polling liên tục), **không có retry** (fail 1 lần là `FAILED` vĩnh viễn, comment trong code ghi rõ), và `SELECT FOR UPDATE SKIP LOCKED` bị vô hiệu hoá 1 phần vì lock thả ngay sau khi đọc chứ không giữ tới lúc xử lý xong — pattern này được thiết kế riêng cho retry tóm tắt cuối ngày (không nhạy thời gian), sai công cụ cho việc cần phản hồi nhanh + có retry thật. Orchestrator đã có sẵn hạ tầng đúng việc hơn: `transcription:stream`/`save_transcription:stream` (Redis Stream, `XREADGROUP` event-driven thật, có `XAUTOCLAIM` tự phục hồi worker crash, có retry/DLQ) — dùng lại y hệt khuôn mẫu này, chỉ thêm 1 stream key mới. DB (`Track.derivative_status`) vẫn là nguồn sự thật về trạng thái; Stream chỉ là cơ chế kích hoạt, không phải nơi lưu trạng thái.
- **D18** — Track/Room lifecycle: thay trigger 1:1, không đổi downstream. Điểm mấu chốt lần theo code thật (`webhook_handler.py`, `transcription_service.py`, `pg_transcript_repository.py`):
  - `egress_started` webhook (tạo Track row, status="pending") → thay bằng `recording.started` từ record-service.
  - `egress_ended`/`EGRESS_COMPLETE` (Track.status="wait_process" + publish `transcription:stream` cho Whisper) → thay bằng `recording.completed` từ record-service. **Không đổi gì ở `transcription:stream`/Whisper/`check_and_complete_room`** — vì các bước đó chỉ quan tâm Track.status đạt đúng giá trị, không quan tâm ai đưa nó tới đó.
  - `EGRESS_FAILED`/`EGRESS_ABORTED` → thay bằng `recording.failed`, đánh dấu track lỗi tường minh (đúng tinh thần D11 — báo trung thực, không im lặng).
  - STT (Whisper) không phụ thuộc bản derivative — trigger ngay từ `recording.completed` (raw), không đợi `audio-processing-service`. 2 vòng đời tách biệt hoàn toàn: STT dùng `Track.status` (đã có), derivative dùng `Track.derivative_status` (mới, độc lập).
- **D19** — Event `room_record_done` (SSE metadata, bắn cho bot/FE client): giữ nguyên loại event đã có sẵn (`metadata_channel.py`), chỉ đổi điểm trigger và **rút payload về notice trần** (không kèm `file_results`/filename/timestamp như hiện tại) — *vì lý do bảo mật*, client tự gọi API riêng (đã có sẵn, ngoài phạm vi audio-ingestion) để lấy path thật.
  - **Cấp room, bắn đúng 1 lần/room** — không bắn theo từng track (khác cách `enqueue()`/`final_room()` hiện tại đang gọi `push_room_record_done` ở 2 nơi one 1 cách hơi tuỳ tiện).
  - Điều kiện bắn = **CẢ 2**: (a) room đã finalize (`status='final_room'`) VÀ (b) mọi track trong room có `derivative_status` ở trạng thái cuối (`completed` hoặc `failed` — track lỗi vĩnh viễn vẫn tính là "xong", không treo mãi). Y hệt công thức `check_and_complete_room()` đang dùng cho STT/summary, chỉ đổi chiều dữ liệu kiểm tra.
  - **Phải kiểm tra điều kiện ở CẢ 2 nơi gọi** (mỗi khi 1 track xong derivative, VÀ khi room finalize) — vì thứ tự xảy ra không cố định: có thể mọi track xong derivative trước khi room finalize, hoặc ngược lại. Bên nào chạm điều kiện sau cùng sẽ là bên thực sự bắn event.
  - **Chống bắn trùng bằng UPDATE có điều kiện** (không phải đọc-rồi-kiểm-tra-rồi-bắn), same kỹ thuật với `final_room_status()` đang dùng (`WHERE status='pending'`) và với race-condition fix ở D16:
    ```sql
    UPDATE rooms SET record_notified_at = :now
    WHERE id = :id AND record_notified_at IS NULL AND status = 'final_room'
      AND NOT EXISTS (
        SELECT 1 FROM tracks WHERE room_ref_id = :id
        AND derivative_status NOT IN ('completed', 'failed')
      )
    ```
    UPDATE ảnh hưởng 1 dòng → bên đó thắng, được bắn SSE; 0 dòng → đã có người bắn hoặc chưa đủ điều kiện, bỏ qua.
- **D20** — Presigned URL cho việc client lấy file thật: **ngoài phạm vi `audio-ingestion`**, người dùng đã có kế hoạch riêng xử lý sau. API "lấy path" cho client cũng đã có sẵn, không cần động vào. Phạm vi ở đây dừng lại ở: bắn `room_record_done` đúng lúc, đúng payload tối giản.
- **D21** — Endpoint `POST /api/v2/recordings/events` bảo vệ bằng `verify_api_key` (Bearer secret) — tái dùng cơ chế đã có (`INTERNAL_API_SECRET`), không phát minh thêm. *Hệ quả*: record-service cần biết secret này — thêm `api_key`/`ORCHESTRATOR_API_KEY` vào `OrchestratorConfig` (record-service) và gửi `Authorization: Bearer` trong `HttpEventReporter`, đúng cơ chế `agents/src/services/orchestrator_client.py` đã dùng cho các API khác.
- **D22** — **Không có event `recording.started` riêng** — khác với dự tính ban đầu ở D18 ("`egress_started` webhook → thay bằng `recording.started`"). *Lý do sửa*: record-service (Phase 1) chưa từng có logic gửi `recording.started` — `StartRecording.execute()` chỉ trả kết quả `accepted`/`rejected` về cho **agent** qua gRPC ack, không báo gì cho orchestrator. Thay vì mở lại code Phase 1 đã ổn định để thêm event mới, tận dụng luôn: `save_track_metadata` đã có sẵn upsert-by-select-then-branch (tự INSERT nếu track chưa tồn tại) — nên `recording.completed`/`recording.failed` tự tạo Track row nếu chưa có, không cần bước "started" riêng. Đánh đổi: không còn thấy track ở trạng thái "đang ghi" trên dashboard/API trước khi ghi xong — chấp nhận được, không phải thứ chặn chức năng.
- **D23** — Giới hạn đã biết, chấp nhận cho v1: `Track.id` = `recording_id` (`room_id:track_id`, do record-service sinh) — nếu edge case "reconnect trễ sau khi grace-period đã claim session" ở D16 xảy ra thật (2 session độc lập, 2 `object_key` khác nhau, cùng `recording_id`), lần `recording.completed` thứ 2 sẽ **ghi đè** thông tin của session đầu trong Postgres (upsert theo PK) dù cả 2 file audio đều đã an toàn trên MinIO — DB chỉ nhớ được bản mới nhất, bản cũ trở thành "mồ côi" (vẫn tồn tại trên MinIO, chỉ mất liên kết trong DB). Chấp nhận được vì: (1) edge case rất hiếm, (2) D16 đã quyết định chưa kích hoạt reconnect thật ở `RecordForwarder` (cùng server vật lý, chưa cần) nên trigger cho case này gần như không xảy ra trong thực tế hiện tại. Cần nhớ lại nếu sau này bật reconnect logic cho `RecordForwarder`.
- **D24** — Soát code lần 2 theo review của người dùng (`record-service` + `agents`), tìm và sửa 4 vấn đề thật (không phải race condition mới, mà là lock/coupling quá thô hoặc thiếu type):
  1. **`StartRecording._create_lock` là lock toàn cục**, serialize việc start của MỌI session (mọi room/track), dù mục đích ban đầu (tránh TOCTOU giữa `registry.get()` và `await create_multipart_upload`) chỉ cần đúng phạm vi 1 `session_id`. Sửa: chuyển thành lock theo từng `session_id`, sở hữu bởi `SessionRegistry.creation_lock()` (refcounted, tự xoá entry khi không còn ai chờ — dict get-or-create không cần khoá riêng vì không có `await` chen giữa, an toàn theo cơ chế cooperative scheduling 1 thread của asyncio).
  2. **`S3BlobStorage._get_client()` lazy init không khoá** — vốn được che giấu tình cờ bởi lock toàn cục ở #1 (mọi session start đều serialize qua đó nên lần init đầu tiên vô tình cũng serialize theo); sau khi #1 đổi sang per-key lock, race này lộ ra thật (nhiều session start lần đầu cùng lúc có thể cùng tạo client, lãng phí không nghiêm trọng). Sửa: khởi tạo `boto3.client` eager trong `__init__` (không có I/O lúc khởi tạo, mọi session đều dùng nên lazy không có lợi ích gì) — bỏ hẳn `_get_client()`.
  3. **`AppendAudio._maybe_annotate_quality`/`_upload_part`** thiếu type hint cho tham số `session` (`# noqa: ANN001`) trong khi phần còn lại của codebase (`finalize.py`) đã type đầy đủ — sửa thành `session: RecordingSession`, bỏ `noqa`.
  4. **`FileSessionStateRepository._lock` là lock chung cho mọi session**, dù mỗi session ghi vào file riêng (không có nhu cầu serialize giữa các session khác nhau). Soát lại thấy tính đúng đắn không phụ thuộc lock này: `_write_atomic` (tmp-file + rename) tự đảm bảo 1 lần ghi nguyên tử, và việc 2 lần ghi *cùng 1 session* không chồng nhau đã được đảm bảo sẵn ở nơi gọi (`AppendAudio`/`StopRecording._finalize` đều giữ `active.lock` — per-session — quanh `save()`). Quyết định bỏ hẳn lock (không chuyển thành per-key, vì service còn nhỏ, số nơi gọi `save()`/`delete()` ít và đã audit hết) — đã ghi rõ trong code (`file_session_state_repo.py` module docstring, mục `FUTURE:`) rằng đây là đánh đổi có ý thức: nếu sau này 1 caller mới quên giữ `active.lock`, hậu quả chỉ là mất 1 bản ghi state cũ hơn (rename vẫn nguyên tử, không corrupt), không phải crash — và nếu rủi ro đó không còn chấp nhận được, hướng sửa là per-key lock giống `SessionRegistry.creation_lock`, không phải quay lại 1 lock chung.

  14/14 test `record-service` vẫn pass sau khi sửa.

  ~~**Đã soát nhưng chưa sửa (ngoài phạm vi yêu cầu)**: `HttpEventReporter._get_client()` (`infra/reporting/`) cũng lazy-init `httpx.AsyncClient` không khoá, cùng loại race như #2 nhưng ở HTTP client thay vì S3 — `ReportEvent` có thể gọi đồng thời từ nhiều session finalize cùng lúc. Đã báo cho người dùng biết, chưa tự ý sửa.~~ **Đã sửa** (commit `732a56e` "Feat: eager init http client", cùng ngày, sau khi báo) — áp dụng đúng cách sửa của #2: khởi tạo `httpx.AsyncClient` eager trong `__init__`, bỏ hẳn `_get_client()`. Đánh dấu lại ở đây vì lúc soát Phase 5 (D28) phát hiện mục này ghi "chưa sửa" đã lỗi thời so với code thật — không có tài liệu nào tự cập nhật khi code thay đổi sau đó, đây là lời nhắc để check code thật thay vì chỉ tin PLAN.md khi có nghi ngờ.

  Đồng thời soát code `agents` (`event_handlers.py`), tìm và sửa 2 vấn đề:
  1. **`on_track_subscribed`: `_start_record_forwarding` phải chờ `_start_transcription_for_speaker_id` chạy xong** (await tuần tự trong cùng `register_and_maybe_start`) — dù đã tách khỏi việc phụ thuộc *giá trị* của toggle STT (D3), vẫn còn phụ thuộc *luồng điều khiển*: nếu bước transcription raise exception, `_start_record_forwarding` sẽ không bao giờ chạy tới — record vô tình bị chặn bởi lỗi ở 1 hệ thống hoàn toàn không liên quan, đúng loại coupling mà D3 đã cố tránh (chỉ ở dạng tinh vi hơn). Sửa: chạy 2 nhánh đồng thời bằng `asyncio.gather`, mỗi nhánh tự bắt exception riêng, không nhánh nào có thể chặn nhánh kia.
  2. **`cleanup_lock` là 1 lock chung** cho cả state của transcription (`pending_tracks`/`active_clients`/`transcription_tasks`) lẫn state của record-forwarding (`track_index`/`record_forward_tasks`) — 2 hệ thống độc lập. Phát hiện cụ thể: `safe_disconnect_all` giữ lock này xuyên suốt lúc `await` disconnect toàn bộ WebSocket STT client (timeout tới 10s) — nghĩa là việc dọn `record_forward_tasks` (chỉ cần `cancel()`, không cần chờ mạng) bị kẹt chờ theo đúng khoảng thời gian đó dù không liên quan gì. Sửa: tách thành `_transcription_lock`/`_record_lock` riêng; `safe_disconnect_all` giờ snapshot + clear + cancel `record_forward_tasks` trước tiên (dưới `_record_lock`, nhanh), rồi mới xử lý phần STT chậm hơn.
- **D25** — Bỏ hẳn `RecordServiceConfig.enabled`/`RECORD_SERVICE_ENABLED` (đảo ngược quyết định giữ lại flag này ở "Ghi chú triển khai thực tế (Phase 2)" mục 5). *Lý do đảo*: LiveKit Egress đã bị xoá hoàn toàn (Phase 3) — không còn đường lùi nào để flag này "rollback" về, nên bản chất nó không còn là "công tắc an toàn" như lý do ban đầu, chỉ còn là 1 cách để service âm thầm ngừng ghi âm hoàn toàn nếu ai đó quên bật ở 1 môi trường (mất dữ liệu im lặng, không triệu chứng rõ ràng). Đúng tinh thần D2 (không giữ feature flag khi hướng đi đã chắc chắn). Việc forward luôn được thử; nếu record-service không reachable ở môi trường nào đó, `RecordServiceClient.new_forwarder` đã tự fail-soft theo từng track (log + trả `None`, không crash agent) — nên bỏ flag không làm giảm độ chịu lỗi. Quy trình go-live: deploy lên dev, verify hoạt động thật, rồi lên production — không giấu sau flag.
- **D26** — **Đưa lại event `recording.started`** — đảo ngược một phần D22 (không phải quay lại chính D18's egress-webhook shape, chỉ là quay lại đúng *hành vi* mà bản implementation LiveKit-egress cũ có: tạo Track row ngay khi bắt đầu ghi, không đợi ghi xong). *Phát hiện lúc soát code `orchestrator_service` cùng người dùng*: `check_and_notify_room_recordings_ready()` (D19) và `check_and_complete_room()` đều dùng pattern `NOT EXISTS (SELECT ... WHERE track chưa terminal)` — pattern này **mù trước 1 track hoàn toàn chưa có row nào trong `tracks`**. Sau D22 (bỏ event "started", chỉ tạo row lazy lúc `recording.completed`/`.failed`), 1 track đang ghi dở dang (chưa báo cáo gì cả) không có row nào → 2 hàm trên coi như "không có gì đang treo", có thể khiến `room_record_done`/summary bắn **sớm hơn thực tế** — trong khi track đó vẫn đang ghi âm/upload. Cấp độ nghiêm trọng: cao (sai lệch dữ liệu bắn cho client, không phải chỉ chậm trễ). Đối chiếu code cũ (`git diff`) xác nhận: `_handle_egress_started` (LiveKit webhook, đã xoá ở D2) từng tạo row với `status="pending"` ngay lúc egress bắt đầu — đúng invariant mà D22 đã vô tình phá.
  - **Sửa**: `record-service`'s `StartRecording.execute()` (khi tạo session **mới**, không phải lúc resume/duplicate-start) bắn `recording.started` tới orchestrator qua `ReportEvent`/`HttpEventReporter` đã có sẵn (generic theo `event: str`, không cần đổi payload shape) — **fire-and-forget** (`asyncio.create_task`, không `await`), vì gRPC ack "đã chấp nhận ghi" cho agent không được phép chờ round-trip HTTP tới orchestrator (đặc biệt tệ nếu orchestrator đang down — sẽ làm chậm start ghi của mọi track). Orchestrator (`recording_event_service.py`) xử lý bằng hàm mới `create_track_placeholder()` — `INSERT ... ON CONFLICT (id) DO NOTHING`, **không** dùng lại `save_track_metadata`'s select-rồi-branch upsert — vì event "started" gửi fire-and-forget có thể tới **sau** `recording.completed`/`.failed` (ghi rất ngắn, hoặc retry), 1 upsert thường sẽ đè ngược 1 row đã terminal về lại "pending" mà sau đó không còn gì đẩy nó đi tiếp nữa. `ON CONFLICT DO NOTHING` cho đúng ngữ nghĩa "tạo nếu chưa có, có rồi thì để yên" bất kể thứ tự tới.
  - Tiện sửa luôn: `recording.failed` giờ cũng gọi `check_and_notify_room_recordings_ready` sau khi set `derivative_status="failed"` — trước đó thiếu, không đối xứng với `handle_derivative_event` (nếu track fail là track cuối cùng của phòng, trước đây sẽ không ai kiểm tra lại điều kiện nữa).
  - **Khoảng hở còn lại, đã cân nhắc, chấp nhận được**: vẫn có 1 khoảng trễ ngắn giữa lúc session bắt đầu và lúc HTTP POST "started" tới nơi (khác với zero) — nhưng co từ "cả cuộc gọi dài" xuống còn "1 round-trip HTTP lúc bắt đầu", đúng mức rủi ro mà bản thiết kế egress cũ (webhook `egress_started`) từng chấp nhận. Nếu POST "started" thất bại hẳn, không cần retry riêng cho nó — event terminal (`recording.completed`/`.failed`) vẫn luôn tới độc lập và tự tạo row nếu chưa có (fallback là hành vi y hệt trước D26 cho riêng session đó).
  - **Lỗ hổng CHƯA xử lý, cố tình note lại để không bị quên** (không phải hệ quả của D26, mà D26 làm nó *khả thi hơn* — trước D26, track chưa terminal thì "vô hình", giờ nó có row thật nên có thể "kẹt" thật ở DB): nếu record-service crash **và mất luôn state file cục bộ** (`file_session_state_repo.py` đã note giới hạn này — filesystem ephemeral, pod reschedule) **trước khi** kịp gửi `recording.completed`/`.failed` cho 1 track đã có placeholder row — track đó nằm `derivative_status='pending'` **vĩnh viễn**, không ai timeout nó, phòng chứa nó không bao giờ hoàn thành `room_record_done`/summary. Hiện **chưa có cơ chế** phát hiện/dọn track "mồ côi" kiểu này ở phía orchestrator (không có timeout, không có reconciliation định kỳ đối chiếu track pending quá lâu với record-service). Đã ghi rõ trong docstring `check_and_notify_room_recordings_ready()` và README `record-service` để không lạc mất — *action item cho vòng test/optimize tiếp theo*: cân nhắc thêm 1 pass định kỳ (kiểu như `RecoverOrphanedSessions` bên record-service) đánh dấu `derivative_status='failed'` cho track pending quá N phút mà record-service xác nhận không còn session sống.
- **D27** — Soát tiếp phần `orchestrator` (room lifecycle, participant tracking) cùng người dùng, tìm ra 1 chuỗi vấn đề liên quan tới nhau, sửa gộp 1 đợt:
  1. **`agents` gửi `room_name` (LiveKit) xuống record-service làm `SessionStart.room_id`, không phải UUID ổn định của orchestrator** — dù field đã đặt tên đúng là `room_id` từ đầu (D3/D18), giá trị lại là `ctx.room.name`. Hệ quả: `recording_event_service.py` phải **resolve lại `room_name → room_id` qua registry (Redis) tại đúng lúc mỗi event tới**, không "chốt cứng" lúc bắt đầu ghi. Vì `room_name` có thể bị 1 cuộc gọi mới tái sử dụng ngay sau khi cuộc cũ kết thúc (registry không có cooldown/TTL, xoá là mất luôn — `room_registry_repository.py`), 1 event trễ (record-service báo `recording.completed`/`.failed`/`recording.started` D26 cho track của cuộc gọi CŨ, có thể trễ tới ~20-45s do `safe_disconnect_all`/grace period) có thể bị gán nhầm sang room MỚI nếu tới sau khi tên phòng đã được đăng ký lại.
     - *Sửa*: agent giờ `register_with_orchestrator()` **trước** `ctx.connect()` (trước đây chạy sau, thậm chí sau cả `subscribe_existing_tracks()` — track có sẵn khi agent join có thể đã forward tới record-service trước khi `room_id` kịp có). `EventHandlers` nhận `room_id` qua constructor, `_forward_track_to_record_service` dùng `self.room_id` (fallback về `ctx.room.name` nếu registration lỡ fail — không để mất khả năng ghi âm chỉ vì 1 lần gọi orchestrator trục trặc).
     - `record-service` **không cần đổi code gì** — field `room_id` vốn đã agnostic với nội dung, chỉ dùng làm khoá `session_id`/prefix S3 (`infra/naming.py::build_object_key`). Đối chiếu code egress cũ (`git show`) xác nhận: hệ thống cũ (LiveKit Egress) cũng dùng `room_id` (UUID) làm prefix S3, không phải tên phòng — nên đổi theo hướng này thực ra là **quay về đúng convention cũ**, không phải lệch chuẩn mới.
     - Phía orchestrator (`recording_event_service.py::_resolve_room_ref_id`): thử parse `payload.room_id` như UUID trước — nếu hợp lệ, coi là room UUID thật, check tồn tại thẳng qua Postgres (`get_room_by_id`), **không cần resolve qua registry nữa** (loại bỏ hẳn lớp race trên). Nếu không phải UUID hợp lệ (agent rơi vào nhánh fallback ở trên) → giữ đường cũ (resolve qua registry theo tên) để tương thích ngược.
  2. **Unregister theo tên, không xác nhận danh tính** — `/unregister` cũ resolve + xoá registry entry chỉ dựa vào `room_name`, không kiểm tra ai đang thực sự sở hữu tên đó lúc xoá. Nếu 1 worker (agent) cũ gọi unregister trễ, sau khi 1 cuộc gọi mới (cùng `room_name`) đã register xong, lệnh xoá này sẽ **xoá nhầm registration của cuộc gọi mới**.
     - *Sửa*: worker tự giữ `room_id` của chính nó từ lúc register (đã có sẵn nhờ mục 1), gửi kèm khi gọi `/unregister`. Endpoint đổi thành **compare-and-delete**: chỉ xoá registry entry nếu giá trị hiện tại của `room_name` vẫn đúng bằng `room_id` do caller gửi lên; nếu khác (tên đã bị cuộc gọi mới chiếm), bỏ qua việc xoá registry (không đụng vào đăng ký mới) nhưng vẫn finalize đúng room CỦA MÌNH theo `room_id` tự có (không resolve lại qua tên). An toàn bất kể unregister trễ bao lâu.
  3. **`/register` đang chờ đồng bộ lệnh gọi LiveKit `list_participants` API** (round-trip ra ngoài, phần tốn thời gian nhất trong toàn bộ handler) trước khi trả `room_id` về cho agent — mâu thuẫn trực tiếp với mục 1 (muốn agent có `room_id` càng sớm càng tốt, trước cả `ctx.connect()`, để không làm chậm agent join phòng).
     - *Sửa*: tách `list_participants` + lưu participant batch ra `asyncio.create_task` chạy nền, trả response (`room_id`) ngay sau khi ghi xong Postgres + Redis. Không đánh đổi tính đúng: vẫn là 1 live query thật (chỉ trễ so với response, không phải snapshot cũ gửi qua mạng như phương án dùng `ctx.room.remote_participants` từng cân nhắc — phương án đó bị loại vì tạo khoảng hở bỏ sót participant join đúng lúc, do webhook `participant_joined` cũng bị gate bởi `is_registered`). Webhook thời gian thực vẫn là lưới an toàn còn lại như thiết kế cũ, không đổi gì.
  4. **Phát hiện thêm lúc soát mục 3**: `save_batch_participants()` (hàm participant batch cũ) có race thật — SELECT tính dedup rồi UPDATE riêng (2 bước), trong khi `save_participant()` (webhook dùng) atomic (1 câu UPDATE, dedup ngay trong `WHERE`). Nếu 1 webhook `participant_joined` chạy xen giữa 2 bước đó của batch save, kết quả là **participant bị trùng lặp trong `rooms.participants`**. Race này *đã tồn tại từ trước* (không phải do việc chuyển sang background task ở mục 3 gây ra — cửa sổ thời gian giữa SELECT và UPDATE gần như không đổi dù chạy đồng bộ hay nền), chỉ là soát tới đúng lúc nên lộ ra.
     - *Sửa*: thêm `save_batch_participants_atomic()` — hàm MỚI (không sửa hàm cũ tại chỗ, để giảm conflict với `develop`), dùng SQLAlchemy `update(Room).values(participants=text(...))` với 1 subquery JSONB (`jsonb_array_elements`/`jsonb_build_object`) lọc trùng ngay trong cùng 1 câu UPDATE Postgres khoá row khi chạy — không còn bước đọc riêng để bị stale. `TranscriptionService.save_participants_batch()` (chỉ 1 caller duy nhất, `/register`) đổi sang gọi hàm mới này.

  Tổng kết: cả 4 điểm trên tuy phát hiện riêng lẻ nhưng đều xoay quanh 1 chủ đề — **vòng đời `room_name` (Redis, mutable, không TTL) không còn khớp với vòng đời thật của 1 cuộc gọi** một khi các bước (ghi âm, unregister, participant tracking) được tách rời/chạy bất đồng bộ với nhau. Hướng sửa chung: chuyển từ "resolve lại theo tên tại thời điểm cần" sang "chốt `room_id` ổn định 1 lần, dùng lại xuyên suốt vòng đời cuộc gọi".
- **D28** — Bắt đầu Phase 5 (`audio-processing-service`), thảo luận phạm vi trước khi code, chốt 4 điểm:
  1. **Sample rate/channels hardcode** (16kHz mono) cho bước transcode ffmpeg — khớp đúng định dạng capture cố định hiện tại (D6), không đọc từ metadata động, giữ đơn giản cho v1.
  2. **API `get_audio_info()` (`orchestrator_service/services/room_service.py`) — mapping lại field ở tầng service, không đổi shape response.** Trước đây field `filename` trỏ tới raw PCM object key (`audio_info["filename"]`, headerless — D6, client không phát được). Giờ trỏ tới `audio_info["derivative_object_key"]` (bản OGG/Opus do `audio-processing-service` transcode xong, ghi qua `pg_track_repository.py::update_track_derivative`) — **cùng tên field `filename`, cùng shape response**, chỉ đổi giá trị bên trong, để **không phải sửa gì ở downstream/client** (yêu cầu tường minh của người dùng). Track chưa có `derivative_object_key` (chưa transcode xong, hoặc raw capture lỗi) bị **bỏ qua hẳn** (không fallback về raw filename — raw PCM không phải thứ client dùng được) — client sẽ thấy track đó xuất hiện sau khi gọi lại API này (trigger bởi `room_record_done`, D19).
  3. **Tái dùng nguyên cơ chế Redis Stream consumer đã có** (`RedisStreamService[T]`, xem D17) thay vì viết mới — nhân bản file như `stt_service`/`orchestrator_service` đã làm với nhau (tiền lệ đã có). Lúc soát lại theo yêu cầu người dùng ("review lại xem cơ chế đấy hoạt động có ổn không"), phát hiện 2 bug nhẹ, **cố tình chưa sửa** (người dùng xác nhận giữ nguyên, chỉ cần note để không quên):
     - **`release_my_pending_tasks()` dùng `XCLAIM` sang 1 consumer "dummy" với ý định "nhả message ra ngay lập tức để ai khác claim được"** — nhưng bản thân hành động `XCLAIM` lại **reset đồng hồ idle-time của message đó về 0**, ngược hẳn với ý định (message vừa "nhả ra" lại trông như vừa mới được claim, phải đợi hết `claim_min_idle_time_ms` — mặc định 60s theo `stt_service/config/app_config.py` — mới bị `claim_orphaned_tasks()` của consumer khác nhặt lại). Hệ quả: khi 1 worker chủ động release pending task lúc shutdown êm, message đó bị "đóng băng" thêm tới ~60-90s thay vì được nhặt lại ngay — **không mất dữ liệu** (message vẫn nằm trong PEL, tự phục hồi sau đúng 1 chu kỳ orphan-recovery), chỉ là chậm trễ.
     - **`_process_task()` — nhánh "đang xử lý rồi, bỏ qua" trả `False` ngay mà không gọi `acknowledge()` lẫn `reject()`** — message ở lại PEL không có hành động gì, phải chờ `claim_orphaned_tasks()` (cùng chu kỳ orphan-recovery ~60-90s) mới được nhặt lại xử lý tiếp. Cùng loại hệ quả: chậm trễ tự phục hồi, không mất dữ liệu.
     - *Vì sao chưa sửa*: cả 2 đều là "bug nhẹ" (self-healing, chỉ delay ~60-90s, không mất dữ liệu, không sai lệch kết quả) — người dùng chủ động chọn không đổi gì ở cơ chế dùng chung này lúc bắt đầu triển khai `audio-processing-service`, để giảm rủi ro động vào code đang chạy ổn định cho `stt_service`. *Nhắc lại nếu sau này cần sửa*: cả 2 đều nằm trong `redis_stream_service.py` (bản dùng chung giữa `stt_service`/`orchestrator_service`, và giờ thêm bản copy ở `audio-processing-service`) — sửa 1 nơi phải nhớ đồng bộ tay sang các bản copy khác (đúng tinh thần ghi chú "đồng bộ tay" đã có ở D15 cho proto).
  4. **Deploy bằng systemd, đúng khuôn mẫu `record-service`** (D14) — `deploy/systemd/audio-processing-service@.service` + env templates + README, tái dùng cấu trúc đã verify (`systemd-analyze`) ở Phase 4, không cần thiết kế lại.
- **D29** — Phát hiện thật lúc deploy dev (không phải soát code chủ động): **Whisper STT fail 100% ngay từ transcription đầu tiên** sau hotfix, `av.error.InvalidDataError: Invalid data found when processing input`. Nguyên nhân là tổ hợp 2 quyết định đúng riêng lẻ nhưng xung đột nhau:
  - D6 đổi format capture của `record-service` sang **raw PCM headerless** (không container/header gì cả) để tối giản critical path.
  - D18 giữ nguyên hành vi cũ: STT (`stt_service/service/whisper_transcription_processor.py`) nhận thẳng `object_key` từ `recording.completed` (`recording_event_service.py` → `transcription_service.handle_recording_completed(filename=payload.object_key)`) và gọi `WhisperModel.transcribe(str(audio_path), ...)` — tức đường dẫn file, không phải mảng đã decode.
  - `faster_whisper.transcribe()` khi nhận đường dẫn file (không phải `np.ndarray`) tự gọi `decode_audio()` → PyAV tự dò container/format của file để giải mã — **raw PCM thô không có header nào để dò**, PyAV fail cứng ngay, không phải lỗi thoáng qua nên retry (3 lần, theo `max_retries`) đều fail giống hệt nhau rồi vào dead-letter queue. Trước hotfix, LiveKit Egress tạo file có container hợp lệ nên PyAV đọc được bình thường — bug này **chỉ lộ ra sau khi đổi sang record-service**, không tồn tại trước đó.
  - **Sửa**: `whisper_transcription_processor.py` tự convert PCM16 → `np.ndarray` float32 chuẩn hoá (`_pcm16_bytes_to_float32`, đúng công thức `decode_audio()` tự làm nội bộ: `astype(np.float32) / 32768.0`), truyền thẳng mảng vào `transcribe()` — theo đúng nhánh `if not isinstance(audio, np.ndarray): audio = decode_audio(...)` trong chính source `faster_whisper`, bỏ qua hẳn bước PyAV tự dò định dạng đang fail. Không đổi gì ở D18 (STT vẫn trigger ngay từ raw, không đợi derivative) — sửa đúng chỗ gây lỗi (cách đọc file), không đảo ngược kiến trúc.
  - **Rủi ro kèm theo, đã chặn**: bỏ qua `decode_audio()` cũng bỏ qua luôn bước **resample** nó tự làm — nếu sample_rate/channels capture thực tế lệch với sample rate Whisper's feature extractor kỳ vọng, giờ sẽ **âm thầm** transcribe sai (audio nghe nhanh/chậm hơn thực tế) thay vì báo lỗi rõ như trước. Thêm sanity-check 1 lần lúc `initialize()` (so `AudioConfig.sample_rate`/`channels` với `whisper_model.feature_extractor.sampling_rate`, raise ngay nếu lệch) để giữ đúng tinh thần D11 ("báo trung thực, không im lặng") — fail nhanh lúc service khởi động thay vì lặng lẽ sai lệch từng transcription.
  - **Phạm vi sửa nằm ngoài `audio-ingestion/`** (đụng `stt_service/`) nhưng bắt buộc phải sửa vì đây là hệ quả trực tiếp của D6 — ghi lại ở đây theo đúng tinh thần "1 tài liệu, không rải rác" của file này.

## 7. Roadmap theo giai đoạn — trạng thái triển khai

- [x] Phase 0 — Thảo luận & chốt kiến trúc (tài liệu này)
- [x] Phase 1 — `record-service` skeleton (Ports & Adapters, gRPC adapter, S3 uploader, recovery 3 tầng, event reporter) — code ở `record-service/`, 12/12 test pass. Xem "Ghi chú triển khai thực tế" ở mục 3.
- [x] Phase 2 — `agents`: thêm nhánh forward PCM sang `record-service`, gắn theo vòng đời track/agent (không phải theo toggle STT realtime, đã sửa sau review) — interop thật đã verify. Xem "Ghi chú triển khai thực tế (Phase 2)" ở mục 5.
- [x] Phase 3 — `orchestrator`: endpoint idempotent nhận event record-service/audio-processing-service, `audio_derivative:stream` (D17), trigger STT/derivative theo D18, `room_record_done` theo D19, xoá code "egress" cũ cả 2 phía — verify end-to-end thật với Postgres+Redis thật, pass. Xem "Ghi chú triển khai thực tế (Phase 3)" ở mục 5 và D21–D23.
- [ ] Phase 4 — Bỏ service `egress` khỏi docker-compose dev/prod; đóng gói `record-service` (và sau này `audio-processing-service`) chạy bằng systemd unit trên host thay vì container ở dev/prod (D14); benchmark CPU theo concurrency để chốt số instance/core cần thiết (D13). **Tooling sẵn sàng**: `deploy/systemd/` (template unit `record-service@.service` + `common.env.example`/`instance.env.example` + README hướng dẫn cài) và `scripts/benchmark_concurrency.py` (D13) — người dùng tự chạy/verify trên dev/prod thật (local không có Docker egress để đối chiếu).
- [x] Phase 5 — `audio-processing-service`: transcode raw → derivative, cập nhật DB. Code + 12/12 test pass (bao gồm test ffmpeg thật). `deploy/systemd/` theo đúng khuôn mẫu record-service (D14/D28 điểm 4). Orchestrator's `get_audio_info()` đã map field `filename` sang derivative (D28 điểm 2). Xem "Ghi chú triển khai thực tế (Phase 5)" ở mục 4 và D28. **Chưa chạy thật với Redis/MinIO/orchestrator thật** (chỉ verify bằng fakes + real ffmpeg) — người dùng tự deploy/verify trên dev/prod thật, cùng cách record-service đã làm ở Phase 4.
- [ ] Phase 6 (tương lai, chưa làm) — `SfuRtpAudioSource` adapter khi SFU/meeting provider mới sẵn sàng, ngắt `record-service` khỏi phụ thuộc `agents`/LiveKit
- [ ] Phase 7 (tương lai, riêng) — Migration `tracks.egress_id` → `recording_id`
