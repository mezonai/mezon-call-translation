# mezon-call-translation × mezon-sfu — Checklist & câu hỏi đàm phán

> Tài liệu sống, dùng để rà soát nội bộ và làm việc với team `mezon-sfu`. Cập nhật trạng thái từng mục khi có câu trả lời/quyết định, không xoá — chỉ đánh dấu.
>
> Bối cảnh: team hạ tầng LiveKit đã bỏ Redis khỏi cụm LiveKit server, dự kiến vài tuần tới bỏ hẳn LiveKit, chuyển sang `mezon-sfu` (SFU tự viết bằng C, io_uring, không dựa trên codebase/giao thức LiveKit). Tài liệu này liệt kê toàn bộ điểm `mezon-call-translation` đang phụ thuộc LiveKit, đối chiếu với những gì `mezon-sfu` đã/chưa hỗ trợ, và các câu hỏi cần chốt trước khi bắt tay build lại.
>
> Nguồn: scan trực tiếp source code 2 repo, không phải suy đoán — số dòng/file kèm theo để tra lại khi cần.
> - Lần scan đầu: `mezon-sfu` @ commit `5cfff1d` (chưa có auth, chưa có event tường minh).
> - **Cập nhật 2026-08-14**: `mezon-sfu` team đã push code mới, HEAD lúc đó `1a8bc61` (đã thêm JWT auth bắt buộc + roster event `room_snapshot`/`peer_joined`/`peer_left`/`peer_updated` + hook event NATS thật thay vì stub). Các mục A1/A3/A7/B1/B3 bên dưới đã được cập nhật lại theo code mới — mục nào còn giữ nguyên nhận định cũ sẽ ghi rõ.
> - **Cập nhật 2026-08-16**: `mezon-sfu` push tiếp 1 đợt lớn (57 commit), HEAD hiện tại `2ec3556`. Đáng chú ý nhất: **camera/screen tách hẳn thành track độc lập** (mỗi peer 3 slot uplink audio/camera/screen thay vì 2), **`msid` giờ nhúng `user_id`/`peer_id`**, thêm **RTP MID header extension** để demux, **`room_id` chỉ lấy từ JWT** (bỏ field `room` khỏi message `join`), thêm message `camera`/`mute` tied với state thật. Việc này **giải quyết luôn A3.5 / phần "chưa giải quyết" ở D2 bước 5** (phân biệt mic/screen) — cập nhật lại các mục liên quan bên dưới. Xem thêm `mezon-sfu/CLAUDE.md` (repo kia) để tra chi tiết giao thức đầy đủ, luôn là nguồn chính khi có sai khác.
> - **Cập nhật 2026-08-20**: HEAD hiện tại `4e58348`. commit `88984d6` đổi NATS hook topic mặc định của chính `mezon-sfu` từ hardcode `SFU_HOOK_EVENT` sang config `nats_hook_topic` (default đổi tên thành `mezon_sfu_hook_event`). Ban đầu tưởng đây là rủi ro tách subject với BE mezon — nhưng **đọc lại đúng code BE mezon thì phát hiện ra bug thật ở phía mình**: hằng số Go bên BE mezon tên là `SFU_HOOK_EVENT` nhưng **giá trị** của nó là `"mezon_sfu_hook_event"`, không phải literal `"SFU_HOOK_EVENT"` — chốt 08-19 ở `D4` đã nhầm tên hằng số với giá trị, khiến `internal/workermanager` subscribe sai subject (`"SFU_HOOK_EVENT"`), **chưa từng nhận được trigger `add`/`delete` thật**. Đã sửa default subject sang `"mezon_sfu_hook_event"` — đúng, và tình cờ trùng luôn với default mới của `nats_hook_topic`, nên "1 subject dùng chung, phân biệt bằng field" vẫn đúng như thiết kế. Xem chi tiết ở C.4 + D4.
> - **Cập nhật 2026-08-18**: HEAD hiện tại `3cb7853`. **Quan trọng nhất cho agent**: `push_to_talk` giờ **tách hẳn khỏi `role_change`**, không đổi role và **không còn bắn hook `publish`/`unpublish` qua NATS nữa** — nếu định dùng hook đó để suy luận "ai đang nói" qua PTT thì hết dùng được, phải đọc `push_to_talk_changed` ở tầng WS hoặc theo dõi RTP thật. Message `publish`/`unpublish` do client tự gửi **đã bị xoá khỏi giao thức**. `share_screen` tắt giờ bắn `unshare_screen` (tên event mới, tách khỏi `share_screen`). SDP downlink **đã revert bỏ `a=ssrc`** (thêm ở đợt 08-16, gây lỗi renegotiate) — chỉ còn `a=msid`, ảnh hưởng trực tiếp recipe D2 bên dưới. Xem `mezon-sfu/CLAUDE.md` mục 10 để tra chi tiết từng commit.
> - **Cập nhật 2026-08-24**: HEAD hiện tại `0c59d96`. 2 breaking change: (1) commit `2e01885` (08-22) thêm field JSON top-level `offer_generation` vào cả `offer` (server→client) lẫn `answer` (client→server), sibling với `sdp`, không nằm trong SDP -- client bắt buộc echo lại đúng giá trị, thiếu/sai bị từ chối thẳng (`missing_offer_generation`/`stale_offer_generation`/`future_offer_generation`) trước khi SFU parse SDP. **Đã fix** ở `agents/internal/signaling/messages.go` (`offerMsg.OfferGeneration`, `answerMsg.OfferGeneration`) + `client.go` (echo trong `dispatch`'s `"offer"` case) + test `TestDecodeOffer`/`TestAnswerEchoesOfferGeneration`. (2) commit `0fd8626` (08-23) vô hiệu hoá hoàn toàn `role_change` (luôn trả `error: role_change_disabled`) -- **không ảnh hưởng agent**, agent chưa từng gửi `role_change` (role cố định lúc `join`, xem C.2 bước 4-5 + `signaling.Dial`'s `role` param). Chi tiết đầy đủ + các thay đổi nhỏ khác (WS close code, H264, SDP audio downlink `a=ssrc` quay lại) ở `mezon-sfu/CLAUDE.md` mục 4.1/4.2/10.

---

## 0. Kết luận nhanh

`mezon-sfu` là **SFU thuần transport** (ICE/DTLS/SRTP/RTP/SVC/GCC — tự viết bằng C), **không phải một platform** như LiveKit. Roadmap nội bộ của họ (vừa xoá `Roadmap.md` khỏi repo) chỉ xoay quanh media pipeline (SVC → TWCC/GCC → layer scheduler → NACK/RTX → PLI/FIR) — **không có mục nào cho**: agent dispatch, data channel, webhook/event thật, REST/admin API, participant identity/metadata. Toàn bộ các phần này, nếu `mezon-call-translation` cần, phải hoặc (a) tự build ở tầng ứng dụng, hoặc (b) đàm phán để SFU team bổ sung — đây chính là mục đích của tài liệu này.

**Trạng thái tổng quan theo mezon-sfu (đã cập nhật theo code 2026-08-14):**
- ✅ Đã có tương đương thật sự: auto-create room khi join; SVC/simulcast + GCC congestion control; publish audio uplink (`role:speaker`) đủ nguyên lý cho agent gửi TTS; **JWT auth bắt buộc cho `join`** (HS256, claims gần giống LiveKit `VideoGrants`); **roster event tường minh** (`room_snapshot`/`peer_joined`/`peer_left`/`peer_updated`) map sẵn `user_id ↔ ufrag ↔ mid_audio/mid_video`; **hook event NATS thật** (`join/publish/unpublish/share_screen/leave`, không còn stub `"{}"`).
- ⚠️ Có nền tảng nhưng chưa đủ dùng: hook event NATS chỉ có 6 loại cơ bản (`join/publish/unpublish/share_screen/unshare_screen/leave`), không có event cho "room tạo/đóng" hay "track thật sự publish ở tầng SDP" — và từ **08-18** cũng không có event nào cho push-to-talk (xem C.4); phân loại audio/video/screen (`share_screen`) hiện chỉ là hook tự báo từ client, server không tự phát hiện; JWT claims chưa có scope `canPublish`/`canSubscribe`/`kind` như LiveKit.
- ❌ Chưa có, phải tự xây 100% ở phía mezon-call-translation (trừ khi SFU team nhận làm): worker/job dispatch, participant "kind" (phân biệt bot/người thật), data channel, REST/gRPC admin API (**[SỬA 08-19]** riêng mute/kick giờ có qua WS message `participant_action`, không phải REST — xem A6.1/C.5), webhook HTTP có chữ ký (thay bằng NATS subscribe), SDK client (ICE/DTLS/SRTP/RTP tự viết vì không có SDK non-browser).

---

## Phần A — Checklist chức năng: mezon-call-translation dùng gì của LiveKit, mezon-sfu có gì

### A1. Kết nối & xác thực

| # | Chức năng dùng | Cách dùng hiện tại (LiveKit) | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A1.1 | JWT AccessToken + `VideoGrants` (`room_join`, `can_publish`, `can_subscribe`) | `api.AccessToken(...).with_grants(...)` — `Architect_MultiClient_Server/agents/main.py:39-46` | ✅ **Đã có (mới)** | **[CẬP NHẬT 08-14]** `join` giờ bắt buộc field `token` — HS256, verify bằng `jwt_secret` chung, claims `identity`/`sub`→user_id, `roomJoin`(bool bắt buộc true), `room`, `exp`, `nbf` — rất gần cấu trúc LiveKit `VideoGrants` (`mezon-sfu/src/protocol/signaling/handshake.c:130-278`). **Thiếu so với LiveKit**: không có `canPublish`/`canSubscribe`/`kind` riêng trong token — quyền publish/subscribe vẫn chỉ dựa vào field `role` trong message `join`, không nằm trong token |
| A1.2 | Participant `kind="agent"` | `new_token.with_kind("agent")` — `main.py:45` | ❌ Chưa có | Chỉ có `role: speaker\|audience`, không phân biệt bot khỏi người thật — xem B6 |
| A1.3 | Signaling protocol (offer/answer, trickle ICE) | Protobuf `SignalRequest/Response` qua SDK `livekit-client`/`livekit-agents` | ⚠️ Có, nhưng khác hẳn | JSON tự chế, server luôn là bên offer, **không trickle ICE** (`signaling.c`, `sdp.c:64`) — không tái dùng SDK LiveKit được, phải viết lại transport layer. Giao thức đã phong phú hơn nhiều so với lần scan đầu — xem catalog đầy đủ ở Phần C |
| A1.4 | Auto-create room khi participant đầu tiên connect | Hành vi mặc định LiveKit server | ✅ **Đã có** | `sfu_room_registry_get_or_create()` — `mezon-sfu/src/room/room_registry.c` |
| A1.5 | Roster/room state khi join (LiveKit trả `room.remoteParticipants` sau connect) | SDK tự điền `ctx.room.remote_participants` | ✅ **Đã có (mới)** | **[MỚI 08-14]** Message `room_snapshot` trả ngay sau khi peer được admit — gồm `self_peer_id`, `participant_count`, `members[]` (mỗi member: `peer_id`, `user_id`, `role`, `ufrag`, `mid_audio`, `mid_video`). Xem Phần C |

### A2. Vòng đời Agent (dispatch/spawn) — gap lớn nhất

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A2.1 | Worker registration + auto job dispatch | `agents.cli.run_app(WorkerOptions(entrypoint_fnc, agent_name, load_fnc, load_threshold))` — `main.py:143-149` | ❌ Chưa có | Không có khái niệm worker pool/job dispatch nào |
| A2.2 | Explicit dispatch API (`create_dispatch`/`list_dispatch`/`delete_dispatch`) | `client.agent_dispatch.*` — `orchestrator_service/services/livekit_client.py:213-289` | ❌ Chưa có | Orchestrator hiện chủ động gọi API này để đảm bảo có agent trong room |
| A2.3 | Load-based worker selection (`load_fnc`, tối đa 30 room/worker) | `main.py:126-130` | ❌ N/A | Toàn bộ logic phải tự build lại ở orchestrator, SFU không tham gia |

### A3. Room / Participant / Track events

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A3.1 | `room.on("track_subscribed")` | `main.py:84`, trigger STT + record forwarding — `event_handlers.py:398-439` | ⚠️ Vẫn qua renegotiate, nhưng nay có roster đi kèm | **[CẬP NHẬT 08-14]** Track mới vẫn chỉ lộ ra qua 1 SDP offer mới (`sfu_signaling_trigger_renegotiation`) — không có event `track_subscribed` riêng — nhưng giờ đi kèm broadcast `peer_joined` (có sẵn `mid_audio`/`mid_video`), nên **không cần tự diff SDP nữa để biết track thuộc peer nào**, chỉ cần diff SDP để biết *khi nào* track thật sự active |
| A3.2 | `room.on("track_unsubscribed")` | `main.py:85` | ⚠️ Tương tự A3.1 | Cùng cơ chế renegotiate; `peer_left`/`peer_updated` (khi đổi role sang audience) báo trước phần nào việc track sắp mất |
| A3.3 | `room.on("participant_disconnected")` | `main.py:86`, dọn task theo `participant.identity` — `event_handlers.py:484-531` | ✅ **Đã có (mới)** | **[CẬP NHẬT 08-14]** Message `peer_left` broadcast tường minh cho các peer còn lại, kèm `user_id`, `ufrag`, `peer_id`, `mid_audio`, `mid_video`, `participant_count` (`signaling.c:271-317`) — không cần tự suy luận qua diff ssrc nữa |
| A3.4 | `participant.identity` / `.name` / `.metadata` / `.attributes` | Định danh người nói, hiển thị UI — `agents/src/utils/participant_identity.py` | ✅ **Đã có phần map user_id↔track (mới)** | **[CẬP NHẬT 08-14]** `room_snapshot`/`peer_joined`/`peer_left`/`peer_updated` đều map sẵn `user_id ↔ ufrag ↔ mid_audio/mid_video` — đủ để gắn nhãn track với đúng user cho STT. **Vẫn thiếu**: `.name`/`.metadata`/`.attributes` dạng chuỗi tuỳ ý như LiveKit — chỉ có `user_id` (số) và `role`, JWT có field `metadata` (đọc được, tối đa 64 ký tự) nhưng chưa thấy dùng để truyền display name |
| A3.5 | `publication.source` (enum CAMERA/MICROPHONE/SCREEN_SHARE) phân biệt mic/screen | `event_handlers.py:123,337` (`source==4` → screen) | ✅ **Đã có (update 08-16)** | Camera/screen giờ là 2 track độc lập, mỗi peer có sẵn `mid_screen` riêng (khác `mid_video`) trong `room_snapshot`/`peer_joined`/`peer_left` — đọc trực tiếp, không cần suy đoán heuristic nữa. Chi tiết: `mezon-sfu/CLAUDE.md` mục 2 |
| A3.6 | `track.sid`/`publication.sid` (track id ổn định) | `_track_id_from_publication` — `event_handlers.py:73-82` | ⚠️ Có `mid`/`ssrc`, chưa chắc ổn định cùng mức | Không có "track id" bền vững kiểu LiveKit SID |

### A4. Media I/O (audio in/out) — không có SDK tương đương

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A4.1 | `rtc.AudioStream.from_track()` — nhận PCM đã decode | `event_handlers.py:149,255` (dùng cho cả STT và record-forward) | ❌ Chưa có SDK | Chỉ có SFU server C, không có client SDK Python/Go. Phải tự làm ICE/DTLS/SRTP/RTP/Opus decode |
| A4.2 | `rtc.AudioSource` + `LocalAudioTrack.create_audio_track()` + `publish_track()` — gửi TTS vào room | `tts_manager.py:256-281` | ⚠️ Có concept publish (`role:speaker`), chưa có SDK | `is_audience=false` → server offer `a=recvonly` cho uplink (`sdp.c:231,262`) — giao thức chấp nhận publish nhưng phải tự encode Opus + đóng gói RTP/SRTP bằng tay |
| A4.3 | Simulcast/adaptive bitrate video | LiveKit SDK tự lo | ✅ Có ở tầng SFU | mezon-sfu có SVC (VP8/VP9/AV1) + GCC/TWCC — không liên quan STT nhưng là phần SFU team làm tốt nhất |

### A5. Data channel / text

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A5.1 | `room.on("data_received")`, topic `lk-chat-topic` | `datachannel_dispatcher.py`, `main.py:122` | ❌ Chưa có | Không có SCTP/DataChannel trong source (đã grep xác nhận không có dòng nào) |
| A5.2 | `local_participant.publish_data(...)` | Gửi trạng thái ngược — `tts_manager.py:321` | ❌ Chưa có | Cùng lý do A5.1 |

### A6. Admin/Management API

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A6.1 | `client.room.list_participants()` | `livekit_client.py:295,326`, `room_registry_api.py:158` | ❌ Chưa có | Không có REST/gRPC admin API nào, chỉ có WS signaling. **[MỚI 08-19]** Không có "list", nhưng đã có 2 hành động moderation qua WS message `participant_action` (`{token: <jwt hành động riêng>}`): mute/kick 1 participant theo `user_id` — xem `mezon-sfu/CLAUDE.md` mục 4.1 |
| A6.2 | `get_participant_detail` (tracks, permission, kind, state...) | `livekit_client.py:313-391` | ❌ Chưa có | — |
| A6.3 | `create_room`/`delete_room`/`mute_published_track`/`update_participant`/`send_data` | Có import trong SDK `livekit-api` nhưng **grep xác nhận orchestrator hiện KHÔNG thực sự gọi** (chỉ dùng A2.2 + A6.1) | N/A hiện tại | Chưa cần map ngay, nhưng sẽ thiếu nếu sau này cần (vd: force-remove 1 participant lỗi) |

### A7. Webhook & event pipeline

| # | Chức năng dùng | Cách dùng hiện tại | mezon-sfu | Ghi chú |
|---|---|---|---|---|
| A7.1 | Nhận webhook HTTP, verify chữ ký JWT (`sha256` claim) | `webhook_auth.py` — `livekit.api.TokenVerifier` + `WebhookReceiver` | ⚠️ Cơ chế khác hẳn, nhưng nay có data thật | Vẫn không có webhook HTTP — publish qua **NATS** topic `SFU_HOOK_EVENT`, consumer phải tự subscribe NATS thay vì nhận HTTP POST có ký. Không có cơ chế verify nguồn gốc message (NATS nội bộ, không ký) — xem Phần C để biết cách subscribe |
| A7.2 | Event `participant_joined` (lưu participant vào DB) | `webhook_handler.py:57-75` | ✅ **Đã có (mới)** | **[CẬP NHẬT 08-14]** Event `join` giờ có payload thật: `{"user_id","room_id","name":"","event":"join"}` (`signaling.c:637-654,888`) — không còn stub `"{}"`. `name` vẫn luôn rỗng (chưa gắn display name) |
| A7.3 | Event `track_published` (log track info) | `webhook_handler.py:77-94` | ✅ **Gần tương đương thật cho camera/screen (từ 08-19)**, ⚠️ audio/role vẫn chỉ là ý định | **[CẬP NHẬT 08-19]** Nhánh `camera`/`share_screen` của `publish`/`unpublish`/`share_screen`/`unshare_screen` giờ verify bằng **RTP thật đến** (`camera_rtp_observed`/`screen_rtp_observed`) trước khi bắn — gần sát nghĩa `track_published` của LiveKit rồi. Nhánh audio/role (`join`, `role_change`) **chưa** được nâng cấp, vẫn chỉ là ý định/state client báo, không verify RTP. message WS `publish`/`unpublish` client tự gửi đã bị xoá khỏi giao thức từ 08-18. **`push_to_talk` không bắn hook này** (đã tách khỏi role_change, xem C.4) |
| A7.4 | ~30 loại event khác của LiveKit (room_started/ended, track_muted, egress_*, participant_active...) — hiện bị ignore hết ngoại trừ A7.2/A7.3 | `webhook_handler.py:45-55` | ❌ Chưa có | Không cấp thiết. Riêng "room_started/ended" vẫn chưa có event NATS tương đương — chỉ suy ra gián tiếp qua event `join` đầu tiên/`leave` cuối cùng của room, không có event "room" độc lập |

### A8. Recording/Egress — đã tự chủ động bỏ LiveKit, không cần đàm phán lại phần này

Theo `audio-ingestion/PLAN.md`, đã thay `LiveKit Egress` bằng gRPC `agent → record-service` tự viết (`recording.proto`, `record_service_client.py`). Điểm duy nhất còn treo là **quyết định kiến trúc Phase 6** (`infra/sfu/rtp_audio_source.py`): record-service tự join mezon-sfu (`role:audience`) hay tiếp tục nhận forward từ agent — xem câu hỏi B4 bên dưới.

### A9. Cấu hình/credentials

`LIVEKIT_URL`, `LIVEKIT_HTTP_URL`, `LIVEKIT_API_KEY/SECRET`, `LIVEKIT_WEBHOOK_API_KEY/SECRET`, `LIVEKIT_AGENT_NAME` (`application_config.py` cả agent & orchestrator) → mezon-sfu dùng `config.ini` (`signaling_port`, `media_port`, `public_host`, `nats_url`), **không có khái niệm API key/secret** vì không có REST API lẫn auth. Cần thay toàn bộ biến `LIVEKIT_*` bằng: WS URL của mezon-sfu + NATS URL — **không có gì thay cho API_KEY/SECRET** trừ khi SFU team tự thêm auth.

---

## Phần B — Câu hỏi cần chốt với team mezon-sfu

### B1. Agent join room như thế nào — **phần lớn đã có câu trả lời, còn vài điểm hở**

- ~~Có event/webhook nào báo "participant đầu tiên join"~~ → **Đã có**: event `join` qua NATS bắn ngay khi JWT hợp lệ (A7.2). Còn thiếu: không phân biệt được "participant đầu tiên của room" với "participant thứ N" chỉ từ event này — phải tự đếm ở orchestrator, hoặc dùng `participant_count` trong `room_snapshot`/`peer_joined` (nhưng đó là message WS, không phải NATS — orchestrator không nhận trực tiếp trừ khi agent forward lại).
- ~~Không có JWT~~ → **Đã có (A1.1)**: câu hỏi giờ đổi thành — **ai cấp `jwt_secret` dùng chung cho mezon-call-translation để tự sinh token** (giống việc trước đây orchestrator giữ `LIVEKIT_API_KEY/SECRET`)? Secret này có tách riêng theo môi trường (dev/staging/prod) không?
- `room_id`/`user_id` đưa vào claims JWT (`room`, `identity`/`sub`) do ai cấp phát — mezon backend hay client tự sinh? Nguồn phát hành token cho **bot** (không phải người dùng thật) là gì — có `user_id` giả định riêng cho agent không, hay dùng chung dải id với người dùng thật?
- Vẫn chưa có cơ chế "mời" 1 client vào room qua API — agent phải tự mở WS + gửi `join` y hệt client thường, không có gì tương đương LiveKit `AgentDispatch`.

### B2. Agent nhận/gửi voice & text vào room

- Agent join `role:"speaker"` để publish TTS (A4.2) — có bị tính như 1 người tham gia bình thường không (chiếm slot, xuất hiện trong `room_snapshot`/`peer_joined` của người dùng thật, echo hook event `publish` như người thật)? Vẫn chưa có khái niệm "kind" cho bot (A1.2) — **còn mở**.
- Uplink tối đa mỗi peer = audio + camera + screen (`SFU_MAX_UPLINK_TRANSCEIVERS=3`) — agent chỉ publish audio có được không, hay bắt buộc mở đủ m-line theo offer mẫu? — **còn mở**.
- **Data channel (A5): vẫn chưa có** — có kế hoạch thêm không, hay chat/text phải đi qua kênh hoàn toàn khác ngoài SFU? — **còn mở, ưu tiên cao**.

### B3. Event agent thoát room / event participant — **đã được trả lời phần lớn bởi update 08-14**

- ~~Có ý định thêm message tường minh participant_joined/left~~ → **Đã có**: `peer_joined`/`peer_left`/`peer_updated` qua WS (không phải NATS — chỉ ai đang giữ kết nối WS trong room mới nhận được, agent phải tự lắng nghe trên chính kết nối của nó, không nhận qua NATS).
- ~~SDP dùng msid, không map với user_id~~ → **Đã có**: `room_snapshot`/`peer_joined`/`peer_left` map sẵn `user_id ↔ ufrag ↔ mid_audio/mid_video` — không cần tự suy luận nữa.
- **Câu hỏi mới phát sinh**: `peer_joined`/`peer_left`/`peer_updated`/`room_snapshot` chỉ tồn tại ở tầng WS signaling, **không** đi qua NATS — nghĩa là chỉ có agent (đang giữ kết nối WS) mới thấy được roster, orchestrator/record-service không thấy trừ khi agent tự forward lại. Có kế hoạch bắn thêm các event này (hoặc rich hơn) qua NATS để các service khác (không giữ WS) cũng theo dõi được không?
- Agent chủ động rời room: đóng WS là đủ để server tự bắn `leave` + `peer_left` cho các peer khác (`signaling.c:701-745`) — **đã rõ, không cần message `leave` tường minh phía client**.

### B4. Luồng record — agent forward hay record-service tự vào room

`record-service` đã để sẵn seam Phase 6 cho việc này (A8). Cần chốt hướng:
- **Hướng A**: agent vẫn là client WebRTC duy nhất, decode RTP → forward PCM qua gRPC như hiện tại (không đổi record-service, nhưng agent phải tự làm transport layer — nặng, xem A4.1).
- **Hướng B**: record-service tự join mezon-sfu bằng `role:"audience"` (chỉ nghe), không qua agent nữa.
- Team SFU có định làm server-side egress (ghi âm nội bộ, kiểu LiveKit Egress) không, hay chủ trương "mọi consumer đều phải là 1 WebRTC client join room"? (hiện tại grep không thấy egress/recording nào trong `mezon-sfu`, nên nhiều khả năng là hướng B)
- Nếu hướng B: có SDK/reference client (ngoài file demo HTML) cho non-browser (Python/Go) join + nhận RTP + decode Opus không, hay phải tự viết ICE/DTLS/SRTP/RTP stack từ đầu?
- ~~Ghi riêng track source: mic|screen~~ → **Đã có (update 08-16)**, xem D2 — không cần hỏi nữa.

### B5. API hỗ trợ agent, room, participant, record

- Có kế hoạch REST/gRPC admin API (list room, list participants, lấy trạng thái room — đối chiếu A6) không? ~~kick participant, mute remote track~~ — **[GIẢI QUYẾT MỘT PHẦN 08-19]** đã có qua WS `participant_action`, không cần hỏi phần đó nữa, xem A6.1.
- TURN: code có sẵn nhánh sinh credentials nhưng `turn_secret` đang rỗng, chưa đọc từ `config.ini` (`signaling.c:40,46`) — có kế hoạch bật thật không? Quan trọng nếu agent/record-service không cùng LAN với mezon-sfu.

### B6. Các điểm khác cần hỏi thêm

- **"Kind" cho bot participant**: nếu không có khái niệm "bot" (A1.2), agent sẽ hiện y hệt user thật trong mọi danh sách participant phía client — có OK về sản phẩm không?
- **Giới hạn concurrency**: `SFU_ROOM_MAX_PEERS` (256 lúc scan đầu, nay đã tăng lên 300) tính luôn cả bot — cần ngưỡng thật để orchestrator throttle đúng, và xác nhận ngưỡng này có tiếp tục đổi khi lên production không.
- **Versioning giao thức JSON signaling**: hiện không có schema/version nào (`json_lite.c` viết tay) — có cam kết giữ ổn định message format trong giai đoạn build lại không?
- **Timeline thật**: roadmap nội bộ SFU (đã xoá `Roadmap.md`) chỉ nói về transport/media pipeline, không có phase nào cho recording/agent-integration/data-channel/management-API. Cần hỏi thẳng: các mục ở trên có nằm trong roadmap trước deadline "vài tuần" không, hay phải tự build phần bù mà không chờ họ.

---

## Phần C — Cách giao tiếp thực tế: WS message catalog & NATS event catalog

> Thêm 2026-08-14 sau khi `mezon-sfu` push JWT auth + roster events. Mục tiêu: đủ để agent bên `mezon-call-translation` tự trả lời 2 câu hỏi cốt lõi — **"agent join room thế nào"** và **"lấy audio để record thế nào"** — mà không cần đọc lại source `mezon-sfu`. Chi tiết implementation đầy đủ hơn (kiến trúc, pipeline, build) nằm ở `mezon-sfu/CLAUDE.md` (repo kia), phần này chỉ tập trung vào giao thức giao tiếp.

### C.1 Hai kênh giao tiếp hoàn toàn tách biệt

1. **WebSocket signaling** (1 kết nối / 1 client) — nơi duy nhất để join room, trao đổi SDP, và nhận roster/event realtime của chính room đó. Chỉ ai đang giữ kết nối WS mới thấy được các message này.
2. **NATS topic `mezon_sfu_hook_event`** (đổi tên 2026-08-20, xem D4 — trước đó hardcode literal `SFU_HOOK_EVENT`) — nơi server publish (fire-and-forget) một số ít sự kiện lifecycle ra ngoài, cho các service **không** giữ kết nối WS (vd: orchestrator) theo dõi. Không có gì đảm bảo về thứ tự/độ tin cậy — client publish, ai subscribe thì nhận, không buffer nếu NATS down lúc publish.

**Hệ quả quan trọng cho kiến trúc**: roster đầy đủ (`room_snapshot`, `peer_joined`, `peer_left`, `peer_updated` — có map `user_id ↔ mid_audio/mid_video`) **chỉ tồn tại ở kênh (1)**. Orchestrator (đang subscribe kênh (2)) sẽ **không** tự thấy được ai đang nói ở mid nào — chỉ agent (đang giữ WS) mới thấy. Nếu orchestrator/record-service cần biết roster mà không tự giữ WS, agent phải chủ động forward lại (giống cách record-service hiện nhận PCM qua gRPC từ agent) — đây là điểm cần quyết định kiến trúc, xem lại B4.

### C.2 Luồng agent join room — từng bước

```
1. Có được jwt_secret dùng chung (xem B1 — ai cấp secret này còn đang hỏi)
2. Tự sinh JWT HS256, claims tối thiểu:
   { "identity": "<user_id agent>", "room": <room_id>, "roomJoin": true, "exp": <unix_ts> }
3. Mở WebSocket tới ws://<public_host>:<signaling_port>
4. Gửi: {"type":"join","token":"<jwt>","role":"speaker"|"audience"}
   - [SỬA 08-16] message "join" KHÔNG còn field "room" nữa — room_id chỉ lấy từ claim "room" trong JWT ở bước 2, gửi "room" ở đây bị server bỏ qua
   - role "speaker": agent CÓ THỂ publish (dùng cho TTS) — server offer uplink a=recvonly
   - role "audience": agent CHỈ nghe, không publish gì — server offer uplink a=inactive
5. Nhận về theo thứ tự:
   a) {"type":"joined","room":...,"iceServers":[...]}
   b) {"type":"offer","offer_generation":0,"sdp":"..."}  <- SDP offer đầu tiên từ server, generation luôn =0
   c) {"type":"room_snapshot","self_peer_id":...,"members":[...]}  <- SAU KHI agent gửi answer hợp lệ
6. Agent tạo PeerConnection, set remote offer, tạo answer, gửi:
   {"type":"answer","offer_generation":0,"sdp":"<answer sdp>"}
   - [SỬA 08-22] "offer_generation" là field JSON riêng, sibling với "sdp", KHÔNG nằm trong nội dung SDP —
     agent phải echo lại đúng nguyên giá trị server gửi trong "offer" tương ứng; thiếu/sai bị từ chối
     thẳng (missing/stale/future_offer_generation) trước khi server parse SDP
7. Từ đây, mỗi khi có người khác vào/ra/đổi role, agent nhận thêm:
   {"type":"peer_joined", ...} / {"type":"peer_left", ...} / {"type":"peer_updated", ...}
   và 1 SDP offer mới (renegotiate, "offer_generation" tăng dần khác 0) — agent phải answer lại mỗi lần,
   echo đúng "offer_generation" của offer đó.
8. Agent rời room: đóng WebSocket là đủ — server tự dọn + báo "leave"/"peer_left" cho phần còn lại,
   không cần gửi message "leave" tường minh.
```

Chi tiết từng field payload — xem bảng đầy đủ ở `mezon-sfu/CLAUDE.md` mục 4 (giao thức) và mục 5 (JWT).

**[2026-08-20, bug thật đã fix]** Bước 3-6 (agent tự gom ICE candidate xong mới gửi `answer`, do mezon-sfu bắt buộc non-trickle ICE) có thể mất **vài giây** trên thực tế (đo được ~5s) trước khi mezon-sfu đăng ký xong answer và bắt đầu trả lời STUN (`signaling.c:1127`, "awaits authenticated STUN bind" — mezon-sfu chủ động withhold STUN response cho tới lúc đó). `pion/ice` mặc định chỉ retry STUN 7 lần/candidate pair (~1.4s) rồi đánh dấu pair `Failed` vĩnh viễn — ngắn hơn nhiều so với ~5s cần thiết, khiến ICE luôn `checking → failed` sau 30s dù mạng/protocol hoàn toàn đúng. Fix ở `internal/rtcagent/peer.go` (`SetICEMaxBindingRequests(50)`, phía agent Go, không cần sửa gì bên mezon-sfu). Chi tiết đầy đủ + cách chẩn đoán: `agents/LOCAL_TESTING.md`.

### C.3 Luồng "lấy audio để record" — theo đúng note đã bàn ở B4, giờ có thêm chi tiết giao thức

Bất kể chọn **Hướng A** (agent forward qua gRPC như hiện tại) hay **Hướng B** (record-service tự join), bên nhận audio đều phải tự làm bước 1-6 ở C.2 với `role:"audience"` (không publish, chỉ subscribe) — **không có API "egress" nào để bỏ qua bước tự làm WebRTC client**. Cụ thể:

- Sau bước 6 (answer xong), client audience nhận **toàn bộ RTP audio/video của mọi speaker khác trong room** trên cùng 1 PeerConnection (SFU tự fan-out full-mesh, không có subscribe chọn lọc từng track).
- Để biết **RTP nhận được (theo `mid`) là của user nào** — dùng đúng mapping đã có ở `room_snapshot`/`peer_joined` (C.1): mỗi member có `mid_audio` gắn với `user_id`. Đây chính là input để điền field `participant_identity` trong gRPC `SessionStart` hiện tại (`recording.proto`) nếu tiếp tục Hướng A, hoặc để tag đúng session nếu record-service tự làm ở Hướng B.
- `source: mic|screen` trong `SessionStart` — **[ĐÃ GIẢI QUYẾT 08-16]** giờ xác định trực tiếp qua vị trí `mid` (slot `base+2` = screen) + `msid`, không cần suy đoán/hỏi SFU team nữa — xem D2.
- RTP nhận được là **audio đã encrypt bằng SRTP** (DTLS-SRTP như WebRTC chuẩn) — bên nhận (record-service nếu đi Hướng B) phải tự làm decrypt + decode Opus, không có giúp đỡ nào từ SFU ngoài phần ICE/DTLS handshake chuẩn.
- **[SỬA 08-20]** SSRC/timestamp **không bị SFU normalize** — RTP nhận được giữ nguyên SSRC gốc của publisher, thuận lợi để đối chiếu chéo với `peer_joined`. Riêng **sequence number thì có** — từ commit mới (`rtp_seq_translate.c`), SFU rewrite seq thành gapless riêng cho mỗi cặp (subscriber, SSRC) trước khi egress (bù việc SVC layer scheduler chủ động bỏ bớt gói theo băng thông từng subscriber). Không phải vấn đề với agent dùng `pion` (tự quản lý seq/jitter theo stream nhận được, không so với publisher gốc) — chỉ đáng chú ý nếu tự parse RTP thô và tính packet-loss dựa trên seq gap: số đó giờ phản ánh loss *sau khi SFU lọc layer*, không phải loss mạng thật của publisher.

### C.4 Catalog đầy đủ — NATS event

**[ĐỔI 08-20]** Subject phía mezon-sfu không còn cứng literal `SFU_HOOK_EVENT` — đọc từ config `[nats] nats_hook_topic`, default đổi sang `mezon_sfu_hook_event`. **[SỬA LẠI cùng ngày]** ban đầu tưởng đây là rủi ro tách subject với trigger BE mezon, nhưng hoá ra là dịp phát hiện bug thật ở phía mình: BE mezon publish trigger lên giá trị hằng số `SFU_HOOK_EVENT` của họ, mà giá trị đó **vốn đã là** `"mezon_sfu_hook_event"` từ trước giờ (chốt 08-19 đọc nhầm tên hằng số Go thành giá trị subject — xem D4). Nên rốt cuộc default mới của mezon-sfu (`mezon_sfu_hook_event`) trùng khớp đúng với subject thật BE mezon dùng — không cần hỏi SFU team set lại gì, "1 subject dùng chung" vẫn đúng, chỉ là tên đúng khác với những gì đã ghi trước đây trong file này. `internal/workermanager` đã sửa default subject theo đúng giá trị này.

Payload chung: `{"user_id": "<int64>", "room_id": "<uint64>", "name": "", "event": "<event>"}`

| event | Bắn khi nào | Ghi chú |
|---|---|---|
| `join` | JWT verify thành công lúc `join` | Bắn cho **mọi** role, kể cả `audience` |
| `publish` | Join với role khác `audience`, đổi role sang `speaker` (theo ý định/state, KHÔNG verify RTP), hoặc camera chuyển active (**[ĐỔI 08-19]** giờ verify bằng RTP thật đến, không còn bắn ngay lúc nhận message `camera` nữa — xem dưới) | message client `{"type":"publish"}` đã bị xoá khỏi giao thức từ 08-18, không còn là nguồn. `push_to_talk` **không** trigger event này (xem dưới) |
| `unpublish` | Đổi role sang `audience`, hoặc camera chuyển không-active (RTP-verified, cùng cơ chế trên) | Tương tự — message client `{"type":"unpublish"}` đã bị xoá; `push_to_talk` không trigger |
| `share_screen` | **[ĐỔI 08-19]** Track screen chuyển active **và RTP thật đã tới** (`screen_rtp_observed`) | Trước đây bắn ngay lúc nhận message `share_screen active:true`, giờ có độ trễ vài trăm ms chờ RTP thật — đáng tin cậy hơn nhiều so với ghi nhận trước đây ("chỉ là ý định client báo") |
| `unshare_screen` | Track screen chuyển không-active (RTP-verified, cùng cơ chế trên) | Trước đây tắt screen share cũng bắn `share_screen` (không phân biệt được), giờ có tên riêng từ 08-18 |
| `leave` | WS đóng và peer từng ở trong room | Bắn 1 lần khi `finish_client_close` chạy |

**[QUAN TRỌNG 08-19]** `publish`/`unpublish`/`share_screen`/`unshare_screen` (nhánh **camera/screen**) giờ được xác nhận bằng RTP thật đến (`camera_rtp_observed`/`screen_rtp_observed`, `ingress.c`) — không còn thuần là "tín hiệu ý định client tự báo" như ghi nhận cũ ở A7.3. **Nhánh audio/role (`join`/`role_change`) thì CHƯA được nâng cấp** — vẫn chỉ là ý định/state, chưa verify RTP audio thật. Nếu cần biết chính xác "audio publisher thật sự có RTP" phải tự detect ở tầng agent (đã làm, dựa trên RTP thật tới qua pion), không trông chờ hook NATS cho phần audio.

**[QUAN TRỌNG 08-18]** `push_to_talk` (message WS, xem C.5/`CLAUDE.md` 4.1) từ giờ **tách hẳn khỏi role** — người audience bấm PTT vẫn là `audience`, không trigger `publish`/`unpublish`/`role_change`/`peer_updated` gì cả, chỉ trả riêng `push_to_talk_changed` ở tầng WS. Nếu logic detect "ai đang nói" của agent định dựa vào hook NATS `publish`/role thì **PTT sẽ vô hình** với agent — phải tự lắng nghe `push_to_talk_changed` qua WS (không có ở NATS) hoặc theo dõi RTP thật đến.

**[MỚI 08-19]** WS message `participant_action` (mute/kick người khác — xem `mezon-sfu/CLAUDE.md` mục 4.1) **không có hook NATS tương ứng** — chỉ trả `participant_action_completed` ở tầng WS cho người gửi, không broadcast qua NATS.

**Không có** (và cần hỏi B4/B5 nếu cần): `room_started`/`room_finished`, event xác nhận track audio (mic) thật sự có RTP, event lỗi/disconnect bất thường riêng biệt với `leave` thường, event NATS cho push-to-talk hay `participant_action`.

### C.5 Catalog đầy đủ — WS message (client ⇄ server)

Xem bảng đầy đủ tại `mezon-sfu/CLAUDE.md` mục 4.1/4.2 (không lặp lại ở đây để tránh 2 nguồn dữ liệu lệch nhau khi giao thức đổi tiếp — chỉ 1 nguồn duy nhất cần đồng bộ).

---

## Phần D — Quyết định kiến trúc đã chốt (nội bộ, không cần đàm phán với SFU team)

> Ghi lại để không lặp lại thảo luận. Cập nhật 2026-08-14, sau khi đọc kỹ giao thức mới.

### D1. B4 — chọn **Hướng A**: agent vẫn là điểm join WebRTC duy nhất, tự forward PCM qua gRPC như hiện tại

Không đổi kiến trúc `record-service` (vẫn nhận qua gRPC `recording.proto`, `infra/sfu/rtp_audio_source.py` của Phase 6 **không cần làm** trong đợt này) — giảm tối đa surface thay đổi trong lúc gấp. Có thể quay lại Hướng B sau nếu cần tách `record-service` khỏi phụ thuộc agent.

### D2. Recipe lấy audio của participant khác để record — **cập nhật 2026-08-16 theo layout `mid` mới, sửa lại 08-18 (SDP `a=ssrc` đã bị revert)**

Offer SDP server gửi cho agent (role `audience`) có cấu trúc `mid` cố định, mỗi peer giờ **3 slot** (audio/camera/screen tách riêng, không còn gộp "video" chung):

```
mid:0 = uplink audio (CHÍNH agent, a=inactive vì audience)
mid:1 = uplink camera (CHÍNH agent)
mid:2 = uplink screen (CHÍNH agent)
mid:3,4,5   -> downlink audio/camera/screen của remote peer slot #1
mid:6,7,8   -> downlink audio/camera/screen của remote peer slot #2
...         -> (base=3, mỗi slot 3-wide, cấp phát động khi peer mới vào)
```

Quy trình runtime — giờ có **3 nguồn độc lập** để xác định user_id (trước chỉ có 1, phải cross-reference qua `peer_joined`):

1. `a=msid:u<user_id>-p<peer_id>` trong mỗi section SDP — **đọc thẳng ra `user_id` không cần tra bảng nào khác**. Đây là nguồn nhanh nhất, ưu tiên dùng làm chính.
2. `mid` RTP header extension — mỗi gói RTP nhận được giờ tự mang `mid` trong extension (SFU đã stamp lúc egress, `rtp_ext.c:sfu_rtp_ext_read_mid`) — dùng để demux **theo từng gói**, đáng tin hơn chỉ dựa SSRC tĩnh lấy 1 lần từ SDP (SSRC có thể đổi giữa các lần renegotiate, `mid` thì không).
3. `room_snapshot`/`peer_joined`/`peer_updated` (`mid_audio`, `mid_video`, `mid_screen`, `is_mute`, `role`) — vẫn cần giữ để lấy các field ngoài `user_id` (role, trạng thái mute...), và để biết `mid` nào tương ứng slot nào khi build bảng.

Cách kết hợp thực tế: build bảng runtime `mid → {user_id, is_screen}` bằng cách join (1) — đọc `user_id` từ `msid` mỗi section SDP — với vị trí `mid` (audio=base, camera=base+1, screen=base+2) → biết ngay `mid` nào là audio, `mid` nào là screen, của user nào, **không cần đợi `peer_joined` nữa cho việc này** (dù vẫn nên giữ để có `role`/`is_mute`). Khi nhận RTP, đọc `mid` extension (nguồn 2) để tra bảng ra `user_id`/`is_screen`.

**[SỬA 2026-08-18]** SDP downlink **không còn `a=ssrc` nữa** (commit `d9cffd4` revert lại phần `07c20e3` thêm hôm 08-17 vì gây lỗi renegotiate) — recipe cũ có nhắc "fallback về SSRC-từ-SDP nếu gói không có `mid` extension", **fallback đó không còn khả thi**, SDP chỉ có `a=msid`, không có SSRC nào để tra. Nếu gặp gói RTP không có `mid` extension (không nên xảy ra với client chuẩn, SFU luôn stamp), cách duy nhất còn lại là học SSRC↔mid từ những gói *có* extension trước đó rồi cache lại theo SSRC, không có nguồn SDP nào để dựa vào nữa.

`source: mic|screen` trong gRPC `SessionStart` giờ set trực tiếp: `mid` ở vị trí `base+2` (screen slot) → `source="screen"`, `base` (audio slot) → `source="mic"`. **Gap này đã đóng**, không cần hỏi SFU team nữa.

### D4. Cơ chế trigger spawn/kill agent — **[CẬP NHẬT 2026-08-17, đảo ngược quyết định trước đó]**, **[SỬA LẠI 2026-08-19, đã có code thật từ BE mezon]**

Quyết định trước (orchestrator tự expose REST API, BE mezon gọi trực tiếp) đã bị team mezon thay đổi. Chốt mới, theo xác nhận trực tiếp từ team mezon:

```
FE mezon (bấm nút) → BE mezon → publish NATS event (start) → orchestrator subscribe
  → agent worker manager → spawn Go agent subprocess → agent tự mở WS join mezon-sfu

FE mezon (bấm dừng) → BE mezon → publish NATS event (stop) → orchestrator subscribe
  → agent worker manager → kill Go agent subprocess theo room_id
```

**[SỬA 2026-08-19]** Có code thật từ BE mezon (`dispatchSfuAgentMessage`/`addAgentDispatch`/`deleteAgentDispatch`), lật lại 2 giả định bên dưới đã ghi hôm 08-17:

- ~~Đây là NATS event riêng, khác hẳn `SFU_HOOK_EVENT`~~ — **SAI**. BE mezon publish thẳng lên cùng subject mà `mezon-sfu` C dùng cho hook event của nó (`mezon-sfu/CLAUDE.md` mục 6, payload `{"user_id","room_id","name","event"}`). Không phải 2 subject riêng — **1 subject dùng chung, phân biệt bằng field**: message của BE mezon có `action` (không có `event`), message của SFU có `event` (không có `action`). Subscriber phải tự lọc theo field, bỏ qua message không khớp shape mình cần thay vì coi là lỗi.
- ~~Tên subject chưa chốt~~ — chốt hôm 08-19 là literal `SFU_HOOK_EVENT`, không phải subject riêng đặt tên `mezon.agent.start`/`stop` như code tạm đang dùng.
  - **[❌ SỬA LẠI 2026-08-20, chốt 08-19 ở trên SAI — đây là bug thật, worker-manager code chưa từng nhận được trigger thật]** Bạn tự đọc lại đúng file định nghĩa hằng số phía BE mezon:
    ```go
    const (
        SFU_HOOK_EVENT = "mezon_sfu_hook_event"
        ...
    )
    func dispatchSfuAgentMessage(nc *nats.Conn, buf []byte) error {
        return nc.Publish(SFU_HOOK_EVENT, buf)
    }
    ```
    Chốt 08-19 đã nhầm **tên hằng số Go** (`SFU_HOOK_EVENT`) với **giá trị string** của nó. Subject thật BE mezon publish lên là `"mezon_sfu_hook_event"`, không phải literal `"SFU_HOOK_EVENT"`. `internal/workermanager/config.go` default `AGENT_DISPATCH_SUBJECT` bị set sai theo chốt cũ (`"SFU_HOOK_EVENT"`) — **đã sửa lại thành `"mezon_sfu_hook_event"`** (commit tiếp theo sau lần cập nhật checklist này). Tình cờ đây cũng đúng bằng default mới của chính `nats_hook_topic` bên `mezon-sfu` (commit `88984d6`, xem ngay dưới) — nên rốt cuộc "1 subject dùng chung, phân biệt bằng field" ở bullet trên vẫn đúng, chỉ là tên subject đúng là `mezon_sfu_hook_event`, không phải `SFU_HOOK_EVENT`. **Rủi ro `nats_hook_topic` bị SFU team đổi khác default vẫn còn** (xem bullet ngay dưới) — vẫn nên hỏi thẳng SFU team để chắc chắn.
- Payload thật, 2 hướng đối xứng qua 1 field `action`:
  ```json
  {"action":"add", "room_id":"<room_id>"}
  {"action":"delete", "room_id":"<room_id>"}
  ```
  Chú ý: `room_id` là **JSON string** (BE mezon dùng `fmt.Sprintf(`"%v"`, channelId)`, có quote), không phải number — parse xong mới convert sang `uint64`.
- **Không có `agent_user_id`, không có `role` trong payload** — trái với thiết kế cũ (`internal/workermanager.StartEvent` kỳ vọng cả 2 field này lấy từ event, và `README.md`/code có ghi rõ chủ đích *"agent_user_id must come from the start event, not be invented here"*). **[2026-08-19, đảo lại quyết định trên]** Vì event chắc chắn không bao giờ có field này, đã bỏ hẳn field `AgentUserID` khỏi `StartEvent`, `Manager.Start` giờ luôn lấy `agent_user_id` từ config (`AGENT_USER_ID_BASE`), lỗi ngay nếu chưa set. Ban đầu định `AGENT_USER_ID_BASE + room_id` (mỗi room 1 id riêng) nhưng verify lại source `mezon-sfu` thì `user_id` **không hề bị enforce unique giữa các room** (scope theo từng WS connection/room, không global) — nên đơn giản hoá lại, dùng thẳng **1 id cố định duy nhất** cho mọi agent đang chạy, giống model bot account thông thường (1 identity dùng chung mọi kênh). Đây vẫn là **workaround tạm** để test local được (bạn tự chọn giá trị `AGENT_USER_ID_BASE`, coordinate với BE mezon sau) — chưa phải quyết định cuối cùng, chưa đóng B1/B6.
  - **[2026-08-19] Convention identity kiểu LiveKit cũ (string `agent-<uuid>`) KHÔNG dùng được** — verify trực tiếp trong `mezon-sfu/src/protocol/signaling/handshake.c`: claim `identity`/`sub` được parse bằng `strtoll()` (`extract_json_int64`, dòng 78-126), bắt buộc **toàn bộ giá trị phải là số** (chấp nhận JSON number hoặc string số thuần như `"999001"`, nhưng 1 ký tự không phải digit ở bất kỳ đâu trong chuỗi cũng làm `strtoll` trả lỗi ngay) — JWT bị từ chối với `missing identity/sub claim` nếu không parse được (dòng 245-246). Khác hẳn LiveKit (chấp nhận identity string tùy ý). BE mezon cần chốt lại bằng **dải số nguyên** riêng cho bot, không phải string UUID.
- Start/stop đối xứng ngay từ đầu — không còn rủi ro "agent chạy mãi không dừng" như lo ngại ban đầu ở B1.
- Agent Go bản thân **không cần biết** cơ chế trigger — chỉ nhận `room_id`/`role`/`jwt_secret` qua env/arg lúc worker manager spawn nó.
- **[2026-08-19] Sharded dispatch + tune lại timeout shutdown/startup** — review lại `worker-manager` phát hiện 2 vấn đề: (1) `handleDispatch` gọi thẳng `Manager.Start`/`Stop` đồng bộ trong callback NATS (chạy tuần tự 1 goroutine) → 1 room `Stop` chậm chặn hết room khác; (2) check "đã có agent chưa" trong `Start()` không atomic, chỉ đúng nhờ NATS xử lý tuần tự (ngầm định, chưa document). Fix: route qua 256 shard cố định (`internal/workermanager/shard.go`), chọn shard bằng `mix64(room_id) % 256` (hash trước rồi mod, không mod trực tiếp — `room_id` không chắc phân bố đều nên mod thẳng có thể tạo hot shard). Cùng lúc phát hiện `AGENT_STOP_TIMEOUT_SECONDS` (10s) nhỏ hơn worst-case thật của `ttsplayer.Player.Close()` (35s, thực ra còn tệ hơn vì nó phát hết cả backlog TTS queue chứ không chỉ đợi 1 utterance) → SIGKILL gần như chắc chắn đã cắt ngang graceful shutdown (`UnregisterRoom`/`ReportTTSCompleted`) trước khi kịp chạy. Sửa theo nguyên tắc: timeout nào chờ việc thật cần thời gian thì giữ, timeout nào chỉ để phòng network/bên kia lỗi thì fail-fast — `Player.Close` giờ chủ động cancel (không chờ thụ động) request TTS đang dở và bỏ hẳn backlog chưa phát; các network-call timeout còn lại (record-service forwarder, orchestrator, WS handshake) đều hạ xuống 2-3s. Worst-case mới: Stop ≈9s, Start ≈8s (chi tiết từng bước xem comment tại từng constant trong code, hoặc README.md phần Design decisions).

### D3. B2/B5 (rewrite agent) — chọn viết lại bằng **Go**, dùng `pion/webrtc` cho tầng transport

Lý do chốt: agent hiện tại không còn phụ thuộc AI-library nào chạy in-process (STT qua WS tới Vosk service riêng, TTS qua HTTP tới service riêng — xác nhận qua `requirements-agent.txt`/`stt_client.py`/`tts_client.py`) — phần thân agent chỉ là glue code (WS/HTTP/gRPC client + VAD tín hiệu), portable tốt sang Go. Lý do giữ Python trước đây (SDK LiveKit Python tiện, AI-library tiện) không còn áp dụng vì SDK LiveKit bị bỏ hoàn toàn và AI đã tách service riêng.

Mapping thư viện:

| Việc cần làm | Go library |
|---|---|
| ICE/DTLS/SRTP/RTP client tự chế (thay SDK LiveKit) | `pion/webrtc` — lưu ý mezon-sfu gửi `a=ice-lite` (`sdp.c:631`), nghĩa là **agent phải là full ICE agent chủ động check connectivity**, SFU chỉ respond |
| WS client (STT) | `gorilla/websocket` hoặc `nhooyr.io/websocket` |
| HTTP client (TTS) | `net/http` |
| gRPC client (record-service, `recording.proto` có sẵn) | `google.golang.org/grpc` — chỉ cần `protoc` lại ra Go stub |
| Mongo/S3 client | `mongo-go-driver`, `aws-sdk-go-v2/service/s3` |
| Tự sinh JWT HS256 để `join` | `golang-jwt/jwt` hoặc `crypto/hmac` chuẩn |
| NATS subscribe (nếu agent cần) | `nats.go` (official) |
| Opus decode (RTP payload nhận từ SFU) | `hraban/opus` (cgo) hoặc `pion/opus` |
| VAD (hiện dùng `numpy`/`scipy`/`librosa`) | **Không có lib 1-1** — viết lại native, logic hiện tại đơn giản (energy-based, `enable_vad` thực ra đang tắt trong code cũ) nên không phải việc lớn |

---

## Trạng thái xử lý (điền khi có kết quả)

| Mục | Trạng thái | Người phụ trách | Ghi chú |
|---|---|---|---|
| B1 | ⚠️ Phần lớn đã rõ (JWT + join flow) | | Còn hở: ai cấp `jwt_secret` (đang chờ mezon-sfu share, tạm dùng placeholder), id range cho bot. Trigger spawn agent: đã chốt qua NATS, xem D4 |
| B2 | ⚠️ Đã chốt rewrite Go (D3) | | Data channel (SFU chưa có) vẫn là gap lớn nhất còn lại, cần hỏi |
| B3 | ✅ Phần lớn đã trả lời bởi update 08-14 | | Còn hở: roster chỉ có ở WS, không có ở NATS |
| B4 | ✅ Đã chốt Hướng A (D1, D2) | | `record-service` không đổi; agent tự parse SDP + mid↔ssrc↔user_id. Còn hở: phân biệt mic/screen (D2 bước 5) |
| B5 | ☐ Chưa hỏi | | TURN vẫn chưa bật thật (cần verify lại theo code mới) |
| B6 | ⚠️ 1 phần đã rõ (`SFU_ROOM_MAX_PEERS`=300) | | "kind" cho bot vẫn chưa có |
