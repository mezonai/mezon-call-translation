# Kế hoạch chỉnh sửa dashboard

Tài liệu này giữ yêu cầu gốc để đối chiếu với các bước triển khai theo mã nguồn dự án. **Làm toàn bộ Acceptance và tạo một commit riêng trước khi bắt đầu Good to have.**

## Yêu cầu gốc của người yêu cầu

### Acceptance

> - Show cả username chỗ transcript, summary, audio cho dễ theo dõi là ai với ai.
> - Status của room đang không phân biệt được đã xong transcript và đã summary xong chắc cần có thay đổi về API để phân biệt
>   API get list room trả thêm 1 field để biết summary done; Dashboard sửa theo dựa vào cờ đấy để badge show not summary
> - Bổ sung thêm cột call duration (finalized_at - created_at) để xem room vào quá ngắn đỡ phải vào check detail
> - Cột completed_at không có ý nghĩa, show finalize at.

### Good to have

> - Bổ sung thêm cột index mỗi trang đánh số từ 1 -> page size.
> - Bổ sung thêm paging ở đầu page cho đỡ phải scroll
> - Login đang chưa chuẩn phải login lại liên tục
> - Login xong ra 1 màn error?
> - Refresh token chưa chuẩn, expired không tự văng ra logout

**Điều chỉnh đã chốt sau yêu cầu gốc:** giữ nguyên badge trạng thái room (ví dụ `Completed`); chỉ hiện thêm badge `Summary done` khi API trả `summary_done === true`. Trong tab Summary, chỉ đổi nhãn người của Action Items sang username; giữ nguyên đoạn văn tóm tắt và không thêm danh sách người tham gia.

## 1. Hiện trạng ban đầu đã đối chiếu

- Frontend dùng React/Vite. `src/App.jsx` ánh xạ `/` tới `RoomList` và `/room/:roomId` tới `RoomDetail`. API tập trung ở `src/services/api.js`.
- `src/components/RoomList.jsx` gọi `getRooms`, hiển thị `room.status`, `created_at`, `completed_at`; phân trang ở cuối bảng, `ITEMS_PER_PAGE = 20`. Nó chưa hiển thị `finalized_at`, thời lượng hoặc tiến độ summary.
- `src/constants/roomStatus.js` và `src/utils/display.tsx` định nghĩa badge trạng thái room. **Không đổi nghĩa `Completed`**: `PgTranscriptRepository.check_and_complete_room()` chuyển room từ `final_room` sang `completed` sau khi các track xử lý xong; summary có tiến độ riêng.
- API `GET /api/v2/rooms` đi qua `orchestrator_service/api/v2/endpoints/room_api.py` → `services/room_service.py` → `services/postgresql/pg_transcript_repository.py`. `RoomData` trong `models/room_models.py` đã có `created_at`, `finalized_at`, `completed_at` và `participants`; chưa có cờ summary.
- `Room.participants` là JSONB chứa `participant_identity` và `username` (`services/postgresql/models.py`, `pg_transcript_repository.py`). `RoomDetail` hiện gọi statistics, summary và audio; transcript hiện in `item.participant_id`, audio in `audioFile.participant_identity`. Hàm `getRoomById` đã tồn tại trong `src/services/api.js` nhưng chưa được `RoomDetail` sử dụng.
- `SummaryService.generate_summary()` tạo bản ghi `rooms_summary` với `summary_data: {}` trước khi LLM hoàn thành, rồi cập nhật `summary_data` khi thành công. Vì vậy **bản ghi tồn tại không có nghĩa là summary đã xong**. Phần `RoomSectionSummary` cũng chỉ là kết quả từng đoạn, không phải summary cuối.
- **Đã kiểm tra bằng dữ liệu thực tế ở bước 1:** một room `completed` có `summary_data` chứa nội dung; các identity trong transcript và audio đều khớp với `rooms.participants`, còn `action_items` chỉ có những người được giao việc. Một room `completed` khác có `summary_data: {}`; room này chỉ hiện badge trạng thái room.
- Một room `final_room` đã có `statistics.finalized_at` nhưng `summary_data: {}`; room này cũng chỉ hiện badge trạng thái room. `finalized_at` đánh dấu thời điểm kết thúc cuộc gọi, không phải hoàn thành summary.
- Trong room `completed` chưa có summary vừa kiểm tra, `statistics.finalized_at` có giá trị nhưng response summary (`data`) trả chuỗi rỗng cho `finalized_at` và `completed_at`; `statistics` không khai báo `completed_at`. Do đó các cột thời gian danh sách phải đọc từ bản ghi room của API list, không đọc từ response summary hoặc suy ra từ statistics.
- Frontend chưa có bộ test tự động trong `package.json`; backend hiện có một số test `unittest` trong `orchestrator_service/tests/`.

Các đường dẫn chính: [`RoomList.jsx`](src/components/RoomList.jsx), [`RoomDetail.jsx`](src/components/RoomDetail.jsx), [`api.js`](src/services/api.js), [`room_models.py`](../orchestrator_service/models/room_models.py), [`room_service.py`](../orchestrator_service/services/room_service.py), [`pg_summary_repository.py`](../orchestrator_service/services/postgresql/pg_summary_repository.py).

## 2. Quy tắc sản phẩm và hợp đồng dữ liệu

1. **Hai badge độc lập:** giữ badge trạng thái room hiện tại. Thêm badge summary cạnh nó ở danh sách phòng. `room.status` không bị đổi nghĩa hoặc đổi giá trị trong API.
2. `summary_done: true` khi summary cuối của room đã được lưu với `summary_data` có nội dung; `false` khi chưa có bản ghi hoặc vẫn là bản nháp rỗng. Không dùng `completed_at`, vì đó là mốc hoàn tất xử lý track.
3. Chỉ hiện badge `Summary done` khi `summary_done === true`. Khi cờ là `false` hoặc chưa có, không hiện badge summary. Badge trạng thái room vẫn hiển thị như cũ.
4. Tên hiển thị lấy từ `room.participants` theo khóa `participant_identity`. Khi thiếu hoặc rỗng, hiện identity gốc; khi thiếu cả hai, hiện `Unknown`. Với username trùng nhau, vẫn giữ identity cạnh tên để phân biệt. **Quyết định của người yêu cầu:** trong tab Summary chỉ đổi nhãn người của Action Items; không thêm danh sách chú giải và giữ nguyên đoạn văn tóm tắt.
5. `Call Duration = finalized_at - created_at`, không dùng `completed_at`. Room chưa kết thúc hoặc timestamp sai/âm hiện `—`; room kết thúc dưới một phút vẫn hiển thị giây.

> [!WARNING]
> Tiêu chí `summary_done` hiện dựa vào dữ liệu `summary_data` được ghi khi luồng tạo summary thành công. Trước khi triển khai cần kiểm tra dữ liệu thực tế/fixture: nếu đã có bản ghi `summary_data` khác rỗng nhưng chỉ là kết quả một phần, phải chuyển sang dấu mốc hoàn tất tường minh và cân nhắc backfill dữ liệu cũ. Không thể dùng `EXISTS rooms_summary` đơn thuần.

## 3. Acceptance — làm trước, cùng một commit

### Bước 1 — Xác nhận dữ liệu mẫu và khóa ghép (dễ, 30–45 phút)

- Lấy các trường hợp mẫu: room `pending`, `final_room`, `completed` nhưng chưa có summary, `completed` đã có summary, room thiếu username, room có nhiều người cùng username, room không có `finalized_at`.
- Kiểm tra `messages[].participant_id`, `action_items` keys và `audioFile.participant_identity` có khớp `room.participants[].participant_identity` hay không. Chỉ đối chiếu bằng identity, không ghép theo thứ tự mảng hoặc username.
- Chốt bộ nhãn badge và cách hiển thị username theo mục 2.

**Kết quả bước 1:** mẫu có summary xác nhận identity ghép đúng ở transcript/audio/action items; mẫu chưa có summary xác nhận `completed` vẫn có thể đi với `summary_data: {}`; mẫu `final_room` xác nhận đã có `finalized_at` vẫn có thể chưa có summary. Chỉ cần hiện thêm badge khi summary hoàn tất. Trường hợp dữ liệu cũ có `summary_data` khác rỗng nhưng chưa hoàn chỉnh vẫn là WARNING ở mục 2 và cần soát trước khi chốt cách tính cờ ở bước 2.

**Ảnh hưởng:** là đầu vào cho các bước 2–5; chưa cần sửa API hoặc UI.

### Bước 2 — Thêm cờ `summary_done` vào API list room (trung bình–khó, 2–3 giờ)

Các file dự kiến: `../orchestrator_service/models/room_models.py`, `../orchestrator_service/services/room_service.py`, `../orchestrator_service/services/postgresql/pg_summary_repository.py` hoặc `pg_transcript_repository.py`, và test backend mới.

1. Tạo model item riêng cho API danh sách (ví dụ `RoomListItemData(RoomData)`) với `summary_done: bool` **bắt buộc**, rồi đổi `RoomListResponse.rooms` sang danh sách model đó. Giữ `RoomDetailResponse.room: RoomData` như cũ để API lấy một room không tự trả `false` sai khi chưa tính cờ.
2. Sau khi truy vấn đúng **một trang room** theo quyền hiện tại, lấy danh sách room ID của trang. Truy vấn `rooms_summary` **một lần cho cả trang** để lấy bản summary mới nhất theo `created_at` cho mỗi room, giống cách API chi tiết summary đang chọn bản mới nhất. Từ bản này, xác định `summary_done` theo `summary_data`; không chạy N truy vấn cho N room và không thay đổi logic lọc/pagination hiện có.
3. Gắn cờ vào từng room tại `RoomService.list_rooms()`. Với trang rỗng, bỏ qua truy vấn summary. Nếu truy vấn summary lỗi, không âm thầm trả `false` cho tất cả room vì như vậy sẽ báo sai trạng thái.
4. Kiểm tra kiểu response thực tế của `GET /api/v2/rooms`: `total`, `limit`, `skip`, `rooms` giữ nguyên; từng phần tử có `summary_done` boolean.

**Ảnh hưởng:** backend và API contract; `RoomList` phụ thuộc trực tiếp. Dự kiến không cần migration nếu tiêu chí ở mục 2 áp dụng được cho dữ liệu hiện có. Không sửa trạng thái room trong DB.

**Kiểm tra bắt buộc:** không có bản ghi summary → `false`; bản nháp `{}` → `false`; summary có nội dung → `true`; phân trang, tìm kiếm và phân quyền danh sách không đổi. Test cả nhánh `can_view_all_rooms` và `list_rooms_by_user`.

### Bước 3 — Badge summary độc lập (dễ–trung bình, 45–75 phút)

Các file dự kiến: `src/components/RoomList.jsx`, `src/utils/display.tsx` hoặc component badge mới, `src/constants/roomStatus.js` nếu cần hằng số nhãn.

- Giữ badge room từ `getStatusBadge(room.status)`; chỉ thêm badge `Summary done` khi `room.summary_done === true`. Giữ nguyên bộ lọc status để nó tiếp tục lọc **trạng thái room**, không lọc summary.
- Khi API cũ chưa trả `summary_done`, không hiện badge summary; backend phải triển khai trước frontend khi phát hành.
- Kiểm tra room `completed` có summary: hiện `Completed` và `Summary done`; room `completed` chưa có summary: chỉ hiện `Completed`.

**Ảnh hưởng:** phần hiển thị danh sách; không sửa `status` hay điều kiện `getRooms`.

### Bước 4 — Username ở transcript, summary, audio (trung bình, 1,5–2,5 giờ)

Các file dự kiến: `src/components/RoomDetail.jsx`, helper hiển thị tên ở `src/utils/` nếu cần. `getRoomById` trong `src/services/api.js` đã có sẵn.

1. `RoomDetail` lấy thêm `GET /api/v2/rooms/id/{roomId}` để nhận `room.participants`; dựng bảng tra `identity → username`. Không lấy username từ user đang đăng nhập, vì đó có thể là người khác với người nói.
2. Transcript: với từng `summary.messages` dùng `item.participant_id` để tra tên; hiện `username` và identity cạnh nhau khi có tên. Không sửa dữ liệu transcript gốc.
3. Audio: dùng `audioFile.participant_identity` để tra cùng bảng; giữ identity cạnh tên và `Unknown`/identity dự phòng. Không đổi URL hoặc cách tải file audio.
4. Summary: đổi nhãn người trong `action_items` bằng phép tra **khớp identity chính xác**. Không thêm danh sách người tham gia riêng trong tab và **giữ nguyên đoạn văn summary**, theo quyết định của người yêu cầu.
5. Lỗi tải thông tin tên không được làm mất transcript/summary/audio đã tải được: hiện identity dự phòng và thông báo phù hợp. Phân quyền vẫn do API hiện có kiểm tra.

**Ảnh hưởng:** trang chi tiết và thêm một request khi mở room; không đổi schema summary hoặc audio. Cần kiểm tra fallback khi `participants` null/rỗng, username null và dữ liệu cũ.

### Bước 5 — Finalized At và Call Duration (dễ, 45–75 phút)

Các file dự kiến: `src/components/RoomList.jsx`, `src/components/RoomDetail.jsx`, `src/utils/datetime.tsx` hoặc helper thời lượng mới.

- Đổi nhãn cột `Completed At` thành `Finalized At` và dùng `room.finalized_at`.
- Trong Overview của trang chi tiết, đổi `Completed At` thành `Finalized At` và dùng `statistics.finalized_at`; response statistics hiện không có `completed_at`.
- Thêm cột `Call Duration` tính từ `created_at` và `finalized_at` theo chênh lệch thời gian thực (không trừ chuỗi ngày). Định dạng `h m s`/`m s`/`s` nhất quán với trang chi tiết nếu phù hợp.
- Cập nhật `TableSkeleton`, `colSpan` của hàng rỗng, khả năng cuộn ngang trên màn hình hẹp. Sau khi thêm cột Good to have ở bước 7, cập nhật chúng lần nữa.

**Ảnh hưởng:** chỉ UI; `RoomData` đã trả cả hai mốc nên không cần API mới.

### Bước 6 — Kiểm tra nghiệm thu và tạo commit Acceptance (trung bình, 1–2 giờ)

- Chạy `npm run build` trong thư mục dashboard; chạy test backend liên quan và bổ sung test cho cờ summary nếu có thể kiểm tra truy vấn với DB test.
- Kiểm tra thủ công luồng danh sách → chi tiết → quay lại danh sách, các badge, tên người, duration, tìm kiếm, lọc status, phân trang, trạng thái loading/error và màn hình hẹp.
- Soát diff để chỉ chứa Acceptance và test liên quan. Tạo **một commit Acceptance**. Không đưa cột index, paging trên đầu hoặc auth vào commit này.

**Tổng ước lượng Acceptance:** khoảng **6–9 giờ**. Bước 6 là mốc bắt buộc trước khi bắt đầu mục 4 dưới đây.

## 4. Good to have — chỉ bắt đầu sau commit Acceptance

### Bước 7 — Cột index từng trang (dễ, 20–30 phút)

- Thêm cột `#` trong `RoomList` với giá trị `index + 1` từ `rooms.map`. Trang 2 bắt đầu lại ở 1 theo yêu cầu, không dùng `currentPage * ITEMS_PER_PAGE + index + 1`.
- Cập nhật skeleton, `colSpan`, kích thước cột và kiểm tra trang cuối có ít hơn 20 room.

**Ảnh hưởng:** chỉ bảng danh sách; không đổi API hay số trang.

### Bước 8 — Phân trang ở đầu bảng (dễ–trung bình, 45–75 phút)

- Tách bộ điều khiển phân trang đang ở cuối `RoomList` thành một component/hàm render dùng chung. Đặt một bản trên bảng, một bản dưới bảng; cả hai nhận `currentPage`, `totalPages` và cùng handler.
- Chỉ hiển thị khi có hơn một trang như hiện tại. Kiểm tra nút Previous/Next, trạng thái disabled, mobile và việc URL giữ lại page/filter.

**Ảnh hưởng:** UI danh sách; không tạo hai bộ state phân trang riêng.

### Bước 9 — Sửa login, callback và refresh token (khó, 3–6 giờ)

Các file dự kiến: `src/contexts/AuthContext.jsx`, `src/services/api.js`, `src/components/Login.jsx`, `src/components/Callback.jsx`, `src/components/ErrorMessage.jsx`; chỉ sửa backend auth nếu kiểm tra chứng minh lỗi ở phía backend.

1. **Tái hiện trước khi sửa:** đăng nhập mới, reload trang, mở tab mới, access token hết hạn, refresh token hết hạn/bị revoke, nhiều request cùng gặp 401, OAuth callback trong dev và build production.
2. **Khôi phục phiên:** `AuthContext` hiện đọc token từ `localStorage` rồi gọi `/auth/mezon/userinfo`; hàm `refreshAccessToken` phụ thuộc state `refreshToken`, dễ gặp giá trị chưa cập nhật trong lần khởi tạo. Sắp lại luồng để refresh dùng token đã đọc được, sau đó cập nhật state và storage đồng bộ; chỉ đưa về login khi refresh thực sự không dùng được.
3. **Interceptor:** `api.js` hiện loại trừ `/auth/mezon/refresh`, nhưng endpoint thực tế là `/auth/refresh`. Sửa điều kiện tránh tự refresh đệ quy; đồng bộ trạng thái auth khi refresh thất bại; chỉ retry request gốc một lần; xử lý nhiều 401 bằng một lần refresh chung.
4. **Callback:** `main.jsx` bật `React.StrictMode`; effect trong `Callback` đổi authorization code và xóa `oauth_state`. Kiểm tra khả năng effect chạy lại gây exchange hai lần hoặc lỗi state, rồi bảo đảm mỗi callback chỉ xử lý một lần mà vẫn giữ kiểm tra CSRF.
5. **Màn lỗi:** `Login`/`Callback` đang truyền `<ErrorMessage message={error} />` trong khi `ErrorMessage` nhận prop `error`. Sửa tên prop và thử các lỗi thực tế để thông báo có nội dung rõ ràng.
6. **Xác minh:** token hợp lệ giữ phiên, access token hết hạn tự refresh, refresh token hết hạn đưa về login và xóa state/storage, callback không báo lỗi giả sau khi đã đăng nhập. Backend trả `expires_in`; cân nhắc dùng để refresh trước hạn nếu cần, nhưng không thêm timer trước khi xác định lỗi gốc.

> [!WARNING]
> Ba triệu chứng login có thể cùng hoặc khác nguyên nhân. Không gộp thành một thay đổi lớn dựa trên phỏng đoán; ghi lại request/response và trạng thái token cho từng trường hợp trước khi sửa. Không đưa token thật vào log hoặc test fixture.

**Ảnh hưởng:** toàn bộ dashboard vì mọi API dùng chung auth client. Nên tạo commit Good to have riêng sau khi kiểm tra hồi quy; nếu auth lớn, tách commit auth khỏi hai cải tiến UI.

## 5. Tiêu chí kết thúc

- Commit Acceptance: giữ badge trạng thái room và chỉ thêm badge `Summary done` khi cờ là `true`; API trả cờ summary đúng với bản nháp và bản hoàn chỉnh; transcript/summary/audio giúp nhận ra username; danh sách có Call Duration và Finalized At; các kiểm tra ở bước 6 đạt.
- Good to have: index bắt đầu từ 1 mỗi trang; phân trang đầu/cuối đồng bộ; login và refresh token qua các kịch bản ở bước 9. Không bắt đầu Good to have trước commit Acceptance.
- Những WARNING còn mở phải được quyết định và ghi lại trước khi thực hiện phần tương ứng.
