# Review plan — `agents/` (Go rewrite)

> Thứ tự review đề xuất: theo layer phụ thuộc, nền tảng trước, ráp nối phức tạp nhất ở gần cuối. `worker-manager` tách riêng vì độc lập với agent. Đánh dấu khi xong, không xoá mục nào — ghi chú phát hiện ngay dưới mục tương ứng.
>
> Đối chiếu nhanh: `mezon-sfu-migration-plan.md` mục 1-2 (đã đánh dấu `[x]`), `mezon-sfu/CLAUDE.md` (giao thức), `agents/README.md` (design decisions + gap đã biết).

## 1. Nền tảng (đọc lướt, ít rủi ro)

- [x] `internal/logging/logging.go` — chỉ cần biết `ErrAttrs` dùng để log kèm type lỗi.
- [x] `internal/config/config.go` — toàn bộ env var + default. Đọc trước để biết agent nhận gì, tránh phải đoán khi đọc code dùng chúng.

## 2. Giao thức mezon-sfu (thuần data shape, dễ đối chiếu đúng/sai)

- [x] `internal/sfuauth/jwt.go` — so với `mezon-sfu/CLAUDE.md` mục 5.
- [x] `internal/signaling/messages.go` — so với mục 4.1/4.2. Sai tên/kiểu field lộ ngay ở đây.
- [x] `internal/signaling/client.go` — trọng tâm: thứ tự dispatch, ping/pong, quyết định "reconnect ở tầng agent chứ không phải ở đây" (đọc comment `Client` doc).

## 3. `rtcagent/peer.go` — nặng nhất, dành thời gian nhiều nhất

- [x] `New()` — chỗ `AddTrack` phải chạy trước `SetRemoteDescription`, đọc kỹ comment giải thích tại sao thứ tự này bắt buộc.
- [x] `handleTrack`/`parseMsid`/`kindForMid` — logic suy ra user_id/kind từ `mid`, so với D2 checklist.
- [x] `HandleOffer` — có đợi `GatheringCompletePromise` không (bắt buộc vì SFU không trickle ICE).
- [x] `readLoop` — chỗ toàn bộ "hợp đồng 1 goroutine sở hữu 1 track" bắt nguồn từ, hiểu kỹ trước khi đọc `audiopipeline`.

## 4. `internal/audiopipeline/bridge.go` — dễ có race nhất, đọc chậm

- [x] Đối chiếu "ai gọi gì từ goroutine nào": `HandlePacket`/`HandleTrackEnded` (goroutine của track) vs `SetSTTEnabled` (goroutine SSE) — lý do duy nhất `session.mu` tồn tại, chỉ bảo vệ `sttSink`.
- [x] `sessionFor`/`applySTTEnabled` — nhánh "thua race, đóng bản trùng" có đúng logic không.

## 5. 2 sink cụ thể — đọc song song, không phụ thuộc nhau

- [x] `internal/recordclient/{client,forwarder}.go` — đối chiếu semantics với `record_service_client.py` gốc (non-blocking, drop-and-report).
- [x] `internal/sttclient/client.go` — đối chiếu với `stt_client.py`, chú ý phần cắt bớt (batching/rate-limit/circuit-breaker/auth) có bỏ sót gì quan trọng không.
- [x] `internal/recordpb/*.pb.go` — chỉ lướt (code generate). Nghi ngờ thì so `proto/recording.proto` với bản gốc bên `audio-ingestion`.

## 6. Nhánh TTS + trigger — phức tạp về luồng điều khiển

- [x] `internal/orchestratorclient/{client,sse}.go` — parse SSE có đúng format Python không; phần `room_name` giả định (đọc kỹ package doc).
- [x] `internal/ttsclient/client.go` — đơn giản, đọc nhanh.
- [-] `internal/opusenc/encoder.go` — chỉ là interface + lỗi rõ ràng; xác nhận đồng ý hướng "để gap rõ ràng" thay vì cgo không kiểm chứng được.
- [x] `internal/ttsplayer/player.go` — hàng đợi `Speak()` (serialize đúng chưa), và `forwardToRecordService` dùng mutex thay vì "1 goroutine sở hữu" như audiopipeline — đọc comment giải thích tại sao khác.

## 7. Ráp nối — đọc cuối cùng khi đã quen hết các mảnh

- [x] `cmd/agent/main.go` — đặc biệt `sessionRefs` (cách SSE handler với vòng đời session ngắn hạn nói chuyện với nhau qua reconnect), và 2 factory `newRecordSinkFactory`/`newSTTSinkFactory`.

## 8. Worker manager — độc lập, review riêng

- [x] `internal/workermanager/config.go`
- [x] `internal/workermanager/events.go`
- [x] `internal/workermanager/manager.go` — trọng tâm: spawn/kill/reap, đoạn `Setpgid`, race đã fix ở nhánh SIGKILL (`<-agent.done` trước khi return).
- [x] `internal/workermanager/subscriber.go`
- [x] `cmd/worker-manager/main.go`

## Sau khi xong

- [ ] Đối chiếu lại với `mezon-sfu-migration-plan.md` mục 1-2 xem báo cáo có khớp thực tế code không.
