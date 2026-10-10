# Hướng dẫn gán nhãn — mezon-whisper

Tài liệu này dành cho người gán nhãn. Mục tiêu: ghi lại **đúng những gì người nói chính đã nói** trong từng đoạn audio, để dùng làm đáp án chấm điểm và dạy model nhận dạng giọng nói.

Mỗi file audio là mic của **một người** trong cuộc họp. Người đó là "người nói chính". Đôi khi mic thu lẫn tiếng người khác vọng ra từ loa: phần đó **không ghi**.

Máy đã điền sẵn một bản nháp. Việc của bạn là **nghe và sửa**, không phải gõ lại từ đầu.

## 1. Quy tắc ghi nội dung

| Tình huống | Cách ghi | Ví dụ |
|---|---|---|
| Nội dung | Ghi đúng lời đã nói. Không sửa ngữ pháp, không thêm từ người nói bỏ sót | Nói "mình deploy cái này rồi" thì ghi đúng như vậy |
| Từ tiếng Anh | Giữ chính tả tiếng Anh, không phiên âm | `deploy`, `pull request`, `staging`, `review` |
| Viết tắt đọc từng chữ | Viết hoa, viết liền | `API`, `CI`, `PR`, `UI` |
| Tên sản phẩm, tên riêng | Viết theo cách chính thức | `Mezon`, `MinIO`, `GitHub` |
| Số | Dùng chữ số | `20`, `3 giờ`, `v2`, `2026` |
| Từ đệm | Bỏ | Bỏ `ờ`, `à`, `ừm`, `uh`, `um` |
| Nói lắp, nói lại | Ghi một lần phần có nghĩa | "mình mình mình nghĩ là" → `mình nghĩ là` |
| Dấu câu | Chỉ dùng `.` `,` `?` | |
| Dấu thanh | Kiểu mới: đặt dấu ở nguyên âm thứ hai | `hoà`, `thuỷ`, `khoẻ` (không phải `hòa`, `thủy`, `khỏe`) |
| Không nghe rõ | Ghi `[?]` vào đúng chỗ không nghe được | `mình sẽ [?] vào thứ 6` |
| Tiếng người khác vọng vào | Không ghi | |
| Chỉ có tiếng ồn, tiếng gõ phím, tiếng thở, tiếng cười | Để trống nội dung | |

Nếu phân vân giữa hai cách viết một từ tiếng Anh hay một tên riêng, hãy hỏi trong nhóm rồi thêm vào danh sách thuật ngữ chung. Cả nhóm phải viết giống nhau.

## 2. Chọn loại (category)

| Loại | Khi nào |
|---|---|
| `vi` | Chỉ có tiếng Việt |
| `mixed` | Tiếng Việt có xen từ tiếng Anh. Ví dụ: "mình merge PR rồi nhé" |
| `en` | Cả câu là tiếng Anh |
| `nonspeech` | Không có lời của người nói chính: im lặng, tiếng ồn, tiếng vọng của người khác, chỉ có từ đệm |

Nếu nội dung để trống thì loại luôn là `nonspeech`.

Chọn `bad_audio` khi audio hỏng (rè nặng, mất tiếng, méo tiếng tới mức không ai nghe được). Đoạn đó sẽ bị loại.

## 3. Hai loại việc

### 3.1. Clip (dự án "train clips")

Mỗi task là một đoạn ngắn, thường từ 1 tới 25 giây.

1. Nghe hết đoạn.
2. Sửa ô nội dung cho đúng với những gì nghe được. Hai dòng "Gợi ý từ máy" bên dưới là kết quả của hai model khác nhau, chỉ để tham khảo.
3. Chọn loại.
4. Bấm **Submit**.

Thứ tự ưu tiên giữa các dự án: `nonspeech_check` → `human_priority` → `human`.

Với dự án `nonspeech_check`: máy nghi đoạn này không có tiếng nói. Nếu đúng là không có lời của người nói chính, xoá hết nội dung và chọn `nonspeech`. Nếu thật ra có người nói, ghi lại như bình thường.

### 3.2. Cửa sổ (dự án "test windows" và "dev windows")

Mỗi task là một đoạn liên tục dài 1–2 phút. Các câu nói được đánh dấu bằng **vùng màu** trên sóng âm.

1. Nghe cả đoạn một lượt.
2. Với từng vùng: bấm vào vùng, sửa nội dung và chọn loại.
3. **Chỉnh mép vùng** cho sát lúc bắt đầu và kết thúc câu nói, lệch không quá 0,1 giây. Kéo mép vùng để chỉnh.
4. Hai câu cách nhau từ 0,5 giây trở lên thì phải là **hai vùng riêng**. Một vùng đang gộp hai câu thì tách ra: thu ngắn vùng cũ rồi kéo chuột tạo vùng mới.
5. Máy bỏ sót một câu thì chọn nhãn `speech` rồi kéo chuột trên sóng âm để tạo vùng mới.
6. Vùng không phải lời của người nói chính (tiếng ồn, tiếng vọng): **để trống nội dung** và chọn `nonspeech`. Không xoá vùng, vì vùng này dùng để đo xem model có "bịa" chữ hay không.
7. Bấm **Submit**.

Phần nằm ngoài mọi vùng được hiểu là "không có lời của người nói chính". Vì vậy đừng bỏ sót câu nào.

### 3.3. Review tập test (lượt hai)

Tập test là đáp án chấm điểm, nên mỗi cửa sổ cần **hai người**: một người sửa, một người khác review.

Người review mở task đã được gán nhãn, nghe lại toàn bộ, sửa những chỗ sai, rồi tick **`reviewed`** ở cuối trang và bấm **Update**. Không review task do chính mình gán nhãn.

## 4. Những lỗi hay gặp

- Sửa lại câu cho "hay hơn" hoặc đúng ngữ pháp hơn. Phải ghi đúng lời đã nói.
- Phiên âm từ tiếng Anh: ghi `đì ploi` thay vì `deploy`.
- Ghi cả tiếng người khác vọng vào mic.
- Để nguyên bản nháp của máy mà không nghe. Bản nháp sai khá thường xuyên, nhất là ở tên riêng và từ tiếng Anh.
- Giữ lại các câu máy tự bịa ở đoạn im lặng, ví dụ "Hãy subscribe cho kênh...", "Cảm ơn các bạn đã theo dõi". Gặp các câu này ở đoạn không có ai nói thì xoá đi.
- Dùng dấu thanh kiểu cũ (`hòa`) lẫn với kiểu mới (`hoà`).
