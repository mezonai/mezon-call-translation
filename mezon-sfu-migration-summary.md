# Tóm tắt bối cảnh — mezon-sfu migration

> File này để 1 session Claude Code khác (hoặc người khác) đọc vào là hiểu ngay bối cảnh, không cần đọc lại toàn bộ lịch sử chat. Đây là **tóm tắt quyết định**, không phải tài liệu kỹ thuật đầy đủ — chi tiết kỹ thuật xem 3 file liệt kê ở mục "Đọc gì tiếp theo".

## Bối cảnh

`mezon-call-translation` (agent STT/TTS join room để phiên dịch/ghi âm cuộc gọi) hiện được xây dựng gần như hoàn toàn dựa trên **LiveKit** (`livekit-agents` SDK, LiveKit server, LiveKit Egress trước đây). Team hạ tầng đang **bỏ hẳn LiveKit trong vài tuần tới**, chuyển sang `mezon-sfu` — 1 SFU **tự viết từ đầu bằng C** (io_uring, lock-free per-room thread), không dựa trên codebase hay giao thức LiveKit. `mezon-call-translation` phải build lại gần như toàn bộ tầng transport + tích hợp.

`audio-ingestion` (`record-service`) đã tự chủ động bỏ LiveKit Egress từ trước, thay bằng agent tự forward PCM qua gRPC (`recording.proto`) — không liên quan tới migration này, giữ nguyên.

## Việc đã làm trong quá trình thảo luận (theo thứ tự)

1. **Scan `mezon-sfu`** — xác định nó là SFU thuần transport (ICE/DTLS/SRTP/RTP/SVC/GCC), không phải platform như LiveKit — không có agent dispatch, data channel, REST admin API, webhook có chữ ký, participant identity/metadata phong phú.
2. **Scan toàn bộ điểm `mezon-call-translation` phụ thuộc LiveKit** (agent + orchestrator + record-service) — liệt kê đầy đủ trong `mezon-sfu-migration-checklist.md` Phần A (9 nhóm: auth, agent dispatch, room/participant events, media I/O, data channel, admin API, webhook, egress, config).
3. **`mezon-sfu` team push code mới giữa chừng** (2026-08-14) — thêm **JWT auth bắt buộc** (HS256, claims gần giống LiveKit `VideoGrants`), thêm **roster event tường minh** (`room_snapshot`/`peer_joined`/`peer_left`/`peer_updated` — map sẵn `user_id ↔ ufrag ↔ mid_audio/mid_video`), thêm **hook event NATS thật** (`join/publish/unpublish/share_screen/leave`, trước đó là stub rỗng). Nhiều gap trong Phần A đã được thu hẹp đáng kể sau update này.
4. Viết `mezon-sfu/CLAUDE.md` — tài liệu kiến trúc + giao thức đầy đủ của `mezon-sfu` (nguồn tham chiếu kỹ thuật chính, cập nhật khi giao thức đổi).
5. Thảo luận sâu, thống nhất 1 số **quyết định kiến trúc** (ghi trong `mezon-sfu-migration-checklist.md` Phần D):
   - **Hướng ghi âm**: giữ nguyên `record-service` (không đổi gì), agent vẫn là điểm join WebRTC duy nhất, tự parse RTP/SSRC để forward PCM qua gRPC như hiện tại (không đi hướng record-service tự join SFU).
   - **Ngôn ngữ agent**: viết lại hoàn toàn bằng **Go**, dùng `pion/webrtc` cho tầng transport (ICE/DTLS/SRTP/RTP) — lý do: SDK LiveKit Python bị bỏ hoàn toàn, STT/TTS đã tách microservice riêng từ trước (agent không còn phụ thuộc AI-library nào chạy in-process), nên không còn lý do giữ Python.
   - **Cơ chế trigger spawn agent**: ~~orchestrator tự expose 1 API, gọi theo chuỗi FE mezon → BE mezon → orchestrator API~~ — **[ĐẢO NGƯỢC 2026-08-17]** team mezon xác nhận đổi sang **NATS event tường minh do BE mezon bắn** (không phải `SFU_HOOK_EVENT`/`join` của `mezon-sfu` — event đó vẫn còn 2 vấn đề cũ: bắn sớm, không phân biệt người đầu/sau). Chuỗi mới: **FE mezon → BE mezon → publish NATS event (start/stop, tên subject chưa chốt) → worker manager subscribe → spawn/kill agent**. Start/stop đối xứng nhau ngay từ đầu (không còn rủi ro "agent chạy mãi"). Từ đây trở đi là chủ động phía `mezon-call-translation`. Chi tiết: `mezon-sfu-migration-plan.md` mục 1.
   - **Vị trí code worker manager**: ~~nằm trong `orchestrator_service` (Python)~~ — **[ĐẢO NGƯỢC lần 2, 2026-08-17]** đổi sang viết bằng **Go, sống chung repo/module với agent** (`agents/cmd/worker-manager`), không phải module trong orchestrator. Lý do: `orchestrator_service` có room registry backed by Redis (bằng chứng chạy multi-instance) — spawn/kill subprocess theo room_id cần 1 instance duy nhất giữ đúng PID, gộp vào orchestrator sẽ cần thêm tầng điều phối phức tạp không cần thiết; ngoài ra tách khỏi vòng đời deploy của tầng API/webhook (deploy thường xuyên hơn) để tránh agent bị kill giữa cuộc gọi. Tư tưởng giống LiveKit `agents` framework: worker (dispatch) + agent (job) chung 1 codebase.
   - **Data channel**: mezon-sfu chưa có, team đó sẽ bổ sung sau — không block, vì hiện chỉ phục vụ luồng phỏng vấn (text/chat), làm ở bước cuối.
   - **Admin/management API** (list room, kick, mute...): mezon-sfu team sẽ làm sau — không block vì `mezon-call-translation` hiện cũng không thật sự cần các API này ngay.

## Việc còn mở (chưa quyết, cần làm rõ trước/khi code)

1. ~~API "stop agent" đối xứng~~ — **[GIẢI QUYẾT 2026-08-17]** đổi sang NATS event, start/stop đối xứng nhau ngay từ đầu, xem trên.
2. **Tên subject NATS chính thức cho start/stop** — tạm đặt tên khi code, cần đồng bộ lại với BE mezon sau (không block).
3. **`jwt_secret` thật** — team mezon-sfu sẽ share sau; hiện `mezon-sfu` **chưa validate chữ ký**, nên tạm dùng placeholder để code/test, không block.
4. **Topology mạng / TURN** — thuộc hạ tầng, do IT helpdesk xử lý riêng, không phải việc của `mezon-call-translation`. Giả định LAN/VPC cho dev.
5. ~~Phân biệt audio mic vs screen-share~~ — **[GIẢI QUYẾT, code `mezon-sfu` 2026-08-16]** camera/screen tách track riêng, xác định thẳng qua vị trí `mid`, không còn là limitation. Xem `mezon-sfu-migration-checklist.md` D2 (cập nhật 08-16).
6. Không có khái niệm "kind" (bot vs người thật) — agent sẽ hiện y hệt user thật trong `room_snapshot`/`peer_joined` phía client. Chấp nhận cho MVP (vấn đề UX/product, không phải kỹ thuật).

## Vài khái niệm nền tảng đã giải thích trong quá trình thảo luận (tra nhanh nếu quên)

- **`ufrag`**: username fragment của ICE — định danh 1 phiên ICE, `mezon-sfu` dùng làm khoá chính để tra session.
- **SDP offer/answer/renegotiate**: `mezon-sfu` luôn là bên gửi offer trước (khác chiều so với nhiều hệ khác); renegotiate = làm lại vòng offer/answer giữa chừng phiên khi room có thay đổi (người vào/ra/đổi role) — client phải answer lại mỗi lần.
- **`mid` numbering cố định** — **[SỬA, layout đổi từ 08-16, số cũ ở đây đã lỗi thời]**: mỗi peer 3 slot (audio/camera/screen tách riêng, không gộp "video"): `mid:0/1/2` = uplink audio/camera/screen của chính client; `mid:3,4,5` = downlink audio/camera/screen của remote peer #1, `mid:6,7,8` của remote peer #2... (base=3, cấp phát tăng dần). Chi tiết + lý do luôn xem `../mezon-sfu/CLAUDE.md` mục 2 (file đó cập nhật theo mỗi lần giao thức đổi, không lặp lại số liệu ở đây nữa để tránh lệch).
- **RTP vs SRTP**: RTP là định dạng gói media (có SSRC, sequence, timestamp); SRTP = RTP + mã hoá/xác thực, khoá derive từ DTLS handshake.
- **SSRC**: id 32-bit gắn với 1 luồng media, dùng để demux khi nhiều luồng đi chung 1 transport (BUNDLE) — `mezon-sfu` forward nguyên SSRC gốc của publisher, không đổi lại.
- **1 phiên WS + 1 phiên UDP/SRTP gắn chặt nhau**: UDP không phải bước độc lập — nó là hệ quả tự động của việc hoàn tất SDP answer qua WS (ICE/DTLS bootstrap từ ufrag/pwd/fingerprint có trong SDP). WS phải sống suốt phiên (để nhận offer renegotiate), không chỉ dùng lúc đầu.
- **"join" (NATS) bắn rất sớm**: ngay khi JWT hợp lệ ở message `join`, **trước** khi SDP/ICE/DTLS/SRTP bắt đầu — không đảm bảo peer đó sau này có media thật. Tín hiệu "chắc chắn" hơn (`peer_joined`/`room_snapshot`) chỉ tồn tại ở kênh WS, không có ở NATS.
- **Recipe lấy audio người khác để record** — **[SỬA, bản cũ ở đây dựa vào SSRC đọc từ SDP, không còn đúng]**: SDP downlink hiện **không có SSRC** (chỉ `a=msid:u<user_id>-p<peer_id>`, dòng `a=ssrc` từng được thêm 08-17 rồi bị revert 08-18 vì gây lỗi renegotiate) — đọc `user_id` thẳng từ `msid`, dùng `mid` RTP header extension (SFU tự stamp mỗi gói) để demux runtime thay vì SSRC. Recipe đầy đủ, luôn cập nhật: `mezon-sfu-migration-checklist.md` mục D2.

## Đọc gì tiếp theo (thứ tự khuyến nghị)

1. `mezon-sfu-migration-summary.md` (chính là file này) — bối cảnh nhanh.
2. `mezon-sfu-migration-plan.md` — việc cụ thể cần làm, theo thứ tự, để bắt tay code ngay.
3. `mezon-sfu-migration-checklist.md` — chi tiết đầy đủ: bảng so sánh từng chức năng LiveKit ↔ mezon-sfu, câu hỏi đàm phán, catalog message/event đầy đủ, quyết định kiến trúc (Phần D).
4. `../mezon-sfu/CLAUDE.md` (repo `mezon-sfu`) — kiến trúc + giao thức đầy đủ của SFU, nguồn tham chiếu kỹ thuật chính khi cần tra lại field/message cụ thể.
