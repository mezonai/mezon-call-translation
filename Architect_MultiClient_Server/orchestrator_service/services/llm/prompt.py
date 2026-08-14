from orchestrator_service.models.summary_models import (
    LightSummaryResult,
    OverallContextResult,
    SummaryResult,
)
from orchestrator_service.models.transcript_models import TranscriptCorrectionResult


def build_prompt_summary(conversation_text: str, language: str) -> str:
    return f"""
# ROLE & OBJECTIVES
You are a professional Project Manager & Technical Writer. Your task is to convert a meeting transcript into Meeting Minutes that strictly adhere to the provided JSON schema.

# SUMMARY RULES
1. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.
2. Always write in the following language: {language}.
3. Participant Naming:
* In next_focus: Use raw [participant_id] (e.g., [hoang.dohuy]). Only use "[Everyone]" when a task is explicitly assigned to the entire team, or "[Unknown]" when it is impossible to clearly identify who the task is assigned to.
* In narrative text (context, key_discussions, detail): Use natural names inferred from transcript context (e.g. "Hoàng"), or fallback to capitalized firstname (e.g. "Hoang"). Never put raw hoang.dohuy inside narrative sentences.
4. Do not invent actions, assignees, or decisions.
5. The `next_focus` field must be the most important part that contains CLEAR and SPECIFIC action items.
6. The `detail` field must be the most comprehensive part, capturing all important information.

# INPUT FORMAT
* The input transcript covers the ENTIRE meeting room conversation.
* The transcript is provided as a list of messages, where each message contains: `content`, `timestamp`, and `participant_id`.
* Note: `participant_id` is provided in standard username format (e.g., `tien.trannhat`, `nguyen.buicao`, `lan.nguyenthi`).

---
{conversation_text}
---

# SUMMARY CONTENT REQUIREMENTS
1. **context**: A concise 2-4 sentence summary stating the meeting's purpose and its most important outcome.
2. **key_discussions**: An array of strings. Each item must follow the format "[Topic Title]\n2-4 sentence summary."
3. **next_focus**: An array of clear action items. If none are explicitly stated → return [].
* This is a VERY IMPORTANT (MOST IMPORTANT) part of the document.
* ONLY extract actual action items (do not extract ideas, questions, or general discussions)
* A task is considered valid if: it describes or mentions a specific action and is referred to as something that needs to be done.
* For each task, accurately identify the `participant_id` responsible for it based on the context of the transcript.
* The structure must strictly follow this format: "[participant_id] Task content. Deadline (if any) and (timestamp)." (Example: "[hoang.dohuy] Check the server logs (09:18:51)")
4. **detail**:
* This is the MOST DETAILED part of the document.
* Each element should be a summary bullet point of a logical topic/discussion.
* Preferred structure: Topic Title: Who + Content + Decision + Technical details + Timestamp.
* Example format: "Xác nhận thông số kỹ thuật ticket 81: Hoàng và team đã thảo luận... (00:02:28)."
* Each bullet point can be 1-4 lines long.

# OUTPUT FORMAT
Return only a valid JSON object matching the SummaryResult schema:

{SummaryResult.model_json_schema()}

# FEW-SHOT EXAMPLES
## Example 1 - Technical Meeting with Specific Action Items
{{
  "context": "Cuộc họp nhằm rà soát tiến độ release cho các task 3094, 3103 và thống nhất quy trình release cuốn chiếu để giảm thiểu rủi ro. Ngoài ra, team cũng thảo luận về luồng xác thực cho tính năng Guest User.",
  "key_discussions": [
    "[Quy trình Release Cuốn Chiếu]\nLeader đề xuất các dev chủ động release sớm các task nhỏ, độc lập đã test trên Staging thay vì dồn tích lại cuối tuần nhằm tránh xung đột code.",
    "[Luồng Xác Thực Guest User]\nThống nhất gửi link mời có đính kèm ID định danh, yêu cầu đăng nhập qua Google Auth để validate và gán nhãn guest trong hệ thống."
  ],
  "next_focus": [
    "[phuong.nguyen] Phối hợp với Morgan để release task 3094 và insert record lên production (09:18:51).",
    "[nam.tranthanh] Fix lỗi search trên cloud cho task 3103 của Confident Project (09:18:51).",
    "[hoang.dohuy] Thiết kế UI bằng storyboard cho tính năng Guest User Invitation (09:31:20)."
  ],
  "detail": [
    "Kế hoạch release Task 3094: Phương báo cáo task 3094 và ES-Link đã hoàn thành, sẵn sàng release và chuẩn bị insert data trên Prod (09:18:51).",
    "Sửa lỗi Task 3103 trên Cloud: Nam đang xử lý task 3103 do gặp lỗi search trên cloud dù chạy local bình thường (09:18:51).",
    "Chỉ đạo quy trình Release Cuốn Chiếu: Bách chỉ đạo dev test kỹ trên Staging và chủ động pin release cuốn chiếu các task nhỏ, ít impact (09:20:56).",
    "Thiết kế UI cho Guest User Invitation: Hoàng đã đọc spec Guest User Invitation và sẽ vẽ storyboard UI flow để trực quan hóa cho team review (09:31:20)."
  ]
}}

## Example 2 - Status Update Meeting Without Action Items (Empty next_focus)
{{
  "context": "Cuộc họp ngắn cập nhật tình hình vận hành hệ thống và thông báo kết quả kiểm thử tính năng thông báo.",
  "key_discussions": [
    "[Báo cáo Vận hành Hệ thống]\nTeam ghi nhận tỷ lệ tài khoản bị khóa đã giảm ổn định sau khi tạm dừng module tự động."
  ],
  "next_focus": [],
  "detail": [
    "Cập nhật tỷ lệ khóa tài khoản hệ thống: Phương báo cáo tỷ lệ tài khoản bị ban đã giảm rõ rệt sau khi tạm dừng tính năng Massadei từ ngày hôm qua (09:49:51).",
    "Kết quả kiểm thử tính năng Notification: Lan xác nhận hệ thống Notification trên Staging chạy ổn định và không phát sinh lỗi mới (09:50:38)."
  ]
}}

## Example 3 - Architecture Discussion with Team Tasks, Unknown Assignee & Deadlines
{{
  "context": "Discussion on database migration plan from PostgreSQL to Qdrant Vector Store and CI/CD security scanning protocols.",
  "key_discussions": [
    "[Database Migration]\nEvaluated Vector Search performance and agreed on the data migration roadmap to Qdrant to optimize RAG retrieval speed.",
    "[CI/CD Security Strategy]\nAgreed to enable automated vulnerability scanning on GitHub Actions and audit GCP access permissions."
  ],
  "next_focus": [
    "[alex.chen] Create a sub-ticket to audit GCP infrastructure security recommendations by 17:00 Oct 25th (09:34:31).",
    "[Everyone] Review and leave feedback on the new Vector DB architecture draft on Notion before Friday (09:40:15).",
    "[Unknown] Sign up for a 3rd-party code scanning service to test integration into the CI/CD pipeline (09:32:56)."
  ],
  "detail": [
    "Automated Security Scan Proposal: Alex proposed automated security scans on every code commit to detect outdated packages (09:32:10).",
    "GCP Infrastructure Audit Requirements: David noted the need to audit open ports and excessive IAM permissions on GCP (09:34:31).",
    "Vector Store Architecture Review Consensus: The team agreed that all members must review the new Vector Store architecture document on Notion prior to next week's sync (09:40:15)."
  ]
}}

# EXECUTION
Analyze the input transcript and only return the JSON object.

# FINAL CHECK
* Check if the content is clean and standardized, and ensure it does not contain any comment artifacts or strange characters generated during the response process like: "//", "//comment ", "] ", "[ ",....
* Re-verify participant names: ensure raw `[participant_id]` is used ONLY in `next_focus`, while narrative fields use natural names (e.g. "Hoàng").
"""


def build_light_summary_prompt(
    conversation_str: str,
    previous_context: str = "",
    language: str = "Vietnamese",
    is_final_section: bool = False,
) -> str:
    if previous_context.strip():
        previous_block = f"""
# PREVIOUS SECTION CONTEXT

{previous_context}
"""
    else:
        previous_block = "# PREVIOUS SECTION CONTEXT\n(No previous context - this is the first section.)"

    return f"""
# ROLE & OBJECTIVES
You are a professional Project Manager & Technical Writer. Your task is to convert a meeting transcript into Meeting Minutes that strictly comply with the provided JSON schema.

{previous_block}

# INPUT TRANSCRIPT
* You will receive a candidate transcript window from a long conversation.
* Each message consists of the following fields: `content`, `timestamp`, `participant_id`
* Note: `participant_id` is provided in standard username format (e.g., `tien.trannhat`, `nguyen.buicao`, `lan.nguyenthi`).

---
{conversation_str}
---

# YOUR TASK
You need to perform 2 tasks within a single response:
1. Detect the first completed topic starting from the first message of the input.
2. Create a Light Summary ONLY for that completed topic.

# BOUNDARY RULES
1. The completed section must start from the first message in the input transcript.
2. `end_message_time` is the `timestamp` of the last message belonging to the first completed topic.
3. Do not cut too early just because of a short status update, an isolated action item, or a change of speaker.
4. If the subsequent messages are still within the same workflow/topic, keep them within one completed section.
5. Only end the section when there is a clear sign that the topic has concluded. (For example: encountering keywords like decision, conclusion, outcome, handoff; or a clear shift to a new, completely unrelated topic).
6. If the first topic is incomplete, return `end_message_time: null` and empty summary fields.
7. If there is a later topic after the completed topic, DO NOT summarize the later topic.
8. Do not return information about incomplete tails or later topics.\n{"9. [CRITICAL] This is the absolute final section of the transcript! You MUST force a completion and summarize the remaining messages. DO NOT return `end_message_time: null`. Select the timestamp of the very last message as `end_message_time`." if is_final_section else ""}

# SUMMARY RULES
1. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.
2. Always write in the following language: {language}.
3. Participant Naming:
* In next_focus: Use raw [participant_id] (e.g., [hoang.dohuy]).
* In narrative text (context, key_discussions, detail): Use natural names inferred from transcript context (e.g. "Hoàng"), or fallback to capitalized firstname (e.g. "Hoang"). Never put raw hoang.dohuy inside narrative sentences.
4. Do not invent actions, assignees, or decisions.
5. The `next_focus` field must be the most important part that contains CLEAR and SPECIFIC action items.
6. The `detail` field must be the most comprehensive part, capturing all important information.
7. Use PREVIOUS CONTEXT to understand the flow, but only summarize the completed section within the current input.

# SUMMARY CONTENT REQUIREMENTS
1. **context**: A concise 1-3 sentence summary stating the purpose of the meeting and its most important outcome.
2. **key_discussions**: An array of strings. Each item must follow the format: "[Topic Title]\n1-3 sentence summary."
3. **next_focus**: An array of clear action items. If none are explicitly stated → return [].
* This is a VERY IMPORTANT (MOST IMPORTANT) part of the document.
* ONLY extract actual action items (do not extract ideas, questions, or general discussions)
* A task is considered valid if: it describes or mentions a specific action and is referred to as something that needs to be done.
* For each task, accurately identify the `participant_id` responsible for it based on the context of the transcript.
* The structure must strictly follow this format: "[participant_id] Task content. Deadline (if any) and (timestamp)." (Example: "[hoang.dohuy] Check the server logs (09:18:51)")
4. **detail**:
* This is the MOST DETAILED part of the document.
* Each element should be a summary bullet point of a logical topic/discussion.
* Preferred structure: Topic Title: Who + Content + Decision + Technical details + Timestamp.
* Example format: "Xác nhận thông số kỹ thuật ticket 81: Hoàng và team đã thảo luận... (00:02:28)."
* Each bullet point can be 1-4 lines long.

# OUTPUT FORMAT
Only return a valid JSON object according to the schema:

{LightSummaryResult.model_json_schema()}

# FEW-SHOT EXAMPLES
## Example 1 - Complete topic, later topic starts after boundary
{{
  "end_message_time": "09:24:38",
  "context": "Cuộc họp thảo luận về tiến độ các task kỹ thuật tồn đọng và thống nhất quy trình release cuốn chiếu để giảm thiểu rủi ro hệ thống.",
  "key_discussions": [
    "[Tiến độ Task & Quy trình Release]\nThống nhất việc chủ động release sớm các task nhỏ, độc lập và đã test trên Staging thay vì dồn tích lại cuối tuần."
  ],
  "next_focus": [
    "[phuong.nguyen] Phối hợp với Morgan để release task 3094 và insert record lên production (09:18:51).",
    "[nam.tranthanh] Fix lỗi search trên cloud cho task 3103 của Confident Project (09:18:51)."
  ],
  "detail": [
    "Tiến độ Task 3094: Phương báo cáo task 3094 đã hoàn thành và sẵn sàng cho đợt release tiếp theo (09:18:51).",
    "Sửa lỗi Task 3103 trên Cloud: Nam đang xử lý task 3103 do gặp lỗi search trên cloud dù local hoạt động bình thường (09:18:51).",
    "Đề xuất Quy trình Release: Bách đề xuất dev chủ động release các task nhỏ, rủi ro thấp để dễ kiểm soát lỗi (09:20:56).",
    "Thống nhất Luồng Release: Khang giải thích hai luồng release hiện tại và thống nhất phương án cho các task độc lập (09:22:37)."
  ]
}}

## Example 2 - First topic is not complete
{{
  "end_message_time": null,
  "context": "",
  "key_discussions": [],
  "next_focus": [],
  "detail": []
}}

## Example 3 - Do not over-split small updates
{{
  "end_message_time": "09:30:54",
  "context": "Summary of minor status updates covering security documentation reviews, Filter UI development, and guest user invitation workflow discussions.",
  "key_discussions": [
    "[Project Updates & Filter UI]\nUpdated on security document submissions and confirmed progress on the Game Manager Filter UI implementation.",
    "[Guest User Invitation Workflow]\nAgreed to distribute direct invitation links to guest users instead of building a complex email infrastructure."
  ],
  "next_focus": [
    "[alex.chen] Complete the Filter UI design for the Game Manager before Friday (09:26:01).",
    "[priya.sea] Share screenshots of the guest invitation UI design with management for review by 17:00 tomorrow (09:30:54)."
  ],
  "detail": [
    "Security Docs & Filter UI Progress: Alex reported that security documents were sent and is currently working on the Filter UI (09:26:01).",
    "Deployment Caution Warning: David advised caution during deployments due to upcoming client holiday schedules (09:28:15).",
    "Guest Invitation Workflow Proposal: Priya proposed sending direct invitation links to guest users to avoid email setup complexity (09:29:06).",
    "Invitation UI Review Request: David requested Priya to share screenshots of the proposed invitation UI for review before 17:00 tomorrow (09:30:54)."
  ]
}}

# EXECUTION
Analyze the input transcript and only return the JSON object.

# FINAL CHECK
* Check if the content is clean and standardized, and ensure it does not contain any comment artifacts or strange characters generated during the response process like: "//", "//comment ", "] ", "[ ",....
* Re-verify participant names: ensure raw `[participant_id]` is used ONLY in `next_focus`, while narrative fields use natural names (e.g. "Hoàng").
"""


def build_overall_context_prompt(section_context_str: str, language: str = "Vietnamese") -> str:
    return f"""
# ROLE
You are a professional Project Manager and Technical Writer.

# YOUR TASK
Create an overall context summary for the entire transcript based on section contexts.

# INPUT SECTION CONTEXTS
---
{section_context_str}
---

# RULES
1. Always write in {language}.
2. Summarize the whole transcript in 3-5 sentences.
3. Do not invent new decisions, actions, or details.
4. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.

# OUTPUT FORMAT
Only return a valid JSON object according to the schema:
```json
{OverallContextResult.model_json_schema()}
```
"""


def build_transcript_correction_prompt(indexed_content: str, previous_context: str = "", language: str = "Vietnamese") -> str:
    if previous_context.strip():
        previous_block = f"""
# PREVIOUS CONTEXT (ALREADY CORRECTED)
Use this context to understand the conversation flow, identify recurring technical terms, proper names, and resolve ambiguous errors.
Do NOT re-correct or include these lines in your output.

{previous_context}
"""
    else:
        previous_block = """
# PREVIOUS CONTEXT (ALREADY CORRECTED)
(No previous context - this is the first section.)
"""

    return f"""
# ROLE & OBJECTIVES
You are a professional Proofreader and Linguist specializing in correcting {language} STT (Speech-to-Text) output from technical meetings.
The STT engine frequently produces Vietnamese phonetic approximations of English words (e.g., "im bặt" for "impact", "cát lụt" for "catalog"). Your primary task is to reconstruct the intended words using context clues, technical domain knowledge, and phonetic reasoning.

{previous_block}

# INPUT FORMAT
* You will receive a block of transcript text from a technical meeting.
* Each line starts with an index in brackets, followed by the content. Example: "[0] xin chào mọi người"
* The meeting context is typically software engineering / IT.

---
{indexed_content}
---

# CORRECTION RULES
1. Correct spelling, grammar, and STT misrecognition errors in {language}.
2. DO NOT rewrite, paraphrase, or change the intended meaning. Only fix errors.
3. When the STT has phonetically transcribed an English word into Vietnamese syllables, reconstruct the original English word if the context makes it clear (e.g., "im bặt" → "impact", "phiếu riêng" → "field riêng", "bắt cấp" → "backup").
4. PRESERVE correctly recognized English technical terms exactly as they appear (e.g., "deploy", "staging", "endpoint").
5. If the input line is already correct, return it unchanged in the output.
6. If a line is completely garbled and you cannot confidently determine the intended meaning, return it unchanged rather than guessing incorrectly.
7. **Proactive anomaly detection**: When you encounter ANY word or phrase that seems out of place, nonsensical, or inconsistent with the surrounding conversation context, actively attempt to infer the intended word using PREVIOUS CONTEXT, surrounding lines, phonetic similarity, and domain knowledge. Apply the correction ONLY if you are reasonably confident; otherwise, leave the original text unchanged.

# COMMON STT ERROR PATTERNS
Pay special attention to these frequent STT misrecognition patterns:

1. **Vietnamese phonetization of English words (Việt hóa âm thanh Tiếng Anh)** — THE MOST COMMON ERROR:
   The STT engine hears English words but writes Vietnamese syllables that sound similar.
   Examples: "im bặt" → "impact", "xten đứt ren" → "extended length", "max zen" → "make sense", "định sệt" → "research", "bắt cấp" → "backup", "cát lụt" → "catalog", "con fidel" → "confident".

2. **English tech term misrecognition (Sai thuật ngữ chuyên ngành)**:
   Common English tech words recognized as unrelated Vietnamese or English words.
   Examples: "phiếu riêng" → "field riêng", "cái phim" → "cái field", "tiktok" → "ticket", "cái bay" → "cái base", "ai cần" → "icon".

3. **Format and abbreviation errors (Sai định dạng chuẩn)**:
   Numbers and abbreviations spelled out phonetically.
   Examples: "ba d" / "ba đề" → "3D", "x ten" → "extended".

4. **Proper name confusion (Sai tên riêng)**:
   Names treated as common words. Use context and PREVIOUS CONTEXT to identify recurring names.
   Examples: "hiểu" → "Hiếu" (person's name), "anh văn" → "Anh Văn" (person's name).

5. **Tone/diacritical errors (Sai dấu/thanh điệu)**:
   Examples: "nó sẽ thừa" → "nó sẽ thường", "nhu" → "nhô", "sữa" → "sửa", "sẻ" → "sẽ".

6. **Filler word misrecognition (Sai từ đệm/cảm thán)**:
   Speech fillers and interjections misrecognized. Preserve natural fillers but fix misrecognized ones.
   Examples: "từ từ từ" → "ừm ừm ừm", "trả" → "chắc là".

7. **Complete hallucination (Ảo giác nhận dạng)**:
   The STT output bears no resemblance to the actual speech. These are UNFIXABLE — leave them unchanged unless surrounding context makes the intended meaning absolutely clear.
   Examples: "mangrioz" → leave as-is (cannot guess), "phút lăng" → leave as-is.

Use the PREVIOUS CONTEXT and surrounding lines to determine the correct word when multiple interpretations are possible.

# OUTPUT FORMAT
Return only a valid JSON object matching the TranscriptCorrectionResult schema:
1. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.
2. The output must contain ALL indices from the input, in the same order.
3. If an index is [5] in the input, it MUST be index 5 in the output JSON.

{TranscriptCorrectionResult.model_json_schema()}

# FEW-SHOT EXAMPLES

## Example 1 — Vietnamese phonetization of English tech terms + tone errors
**Input:**
[0] hôm nay mình sẻ bàn về cái tiktok số 81 nhé
[1] cái phiếu xten đứt ren đang có im bặt lớn, cần sữa gấp
[2] ừ, kiểm tra lại cái cát lụt xem có max zen không
[3] dạ em sẽ bắt cấp dữ liệu trước khi sữa

**Output:**
{{
  "entries": [
    {{"index": 0, "corrected_content": "hôm nay mình sẽ bàn về cái ticket số 81 nhé"}},
    {{"index": 1, "corrected_content": "cái field extended length đang có impact lớn, cần sửa gấp"}},
    {{"index": 2, "corrected_content": "ừ, kiểm tra lại cái catalog xem có make sense không"}},
    {{"index": 3, "corrected_content": "dạ em sẽ backup dữ liệu trước khi sửa"}}
  ]
}}

## Example 2 — Proper name confusion + filler words + tone errors
**Input:**
[10] hiểu hiểu đang đưa lên rồi anh ơi
[11] ừm, nó sẽ thừa đi một chút đó
[12] từ từ từ, để em xem lại
[13] anh văn mới chịu mà, nào nhỉ

**Output:**
{{
  "entries": [
    {{"index": 10, "corrected_content": "Hiếu Hiếu đang đưa lên rồi anh ơi"}},
    {{"index": 11, "corrected_content": "ừm, nó sẽ thường đi một chút đó"}},
    {{"index": 12, "corrected_content": "ừm ừm ừm, để em xem lại"}},
    {{"index": 13, "corrected_content": "Anh Văn mới chỉ mà, ảo nhỉ"}}
  ]
}}

## Example 3 — Mix of fixable errors and unfixable hallucinations
**Input:**
[20] ok anh ơi, em push code lên branch develop rồi
[21] mangrioz cái con fidel đi
[22] dạ em sẻ kiểm tra lại cái ba đề trước
[23] phút lăng cái đó xem

**Output:**
{{
  "entries": [
    {{"index": 20, "corrected_content": "ok anh ơi, em push code lên branch develop rồi"}},
    {{"index": 21, "corrected_content": "mangrioz cái confident đi"}},
    {{"index": 22, "corrected_content": "dạ em sẽ kiểm tra lại cái 3D trước"}},
    {{"index": 23, "corrected_content": "phút lăng cái đó xem"}}
  ]
}}

# EXECUTION
Analyze the input transcript and only return the JSON object.

# FINAL CHECK
* Verify that EVERY index from the input appears in the output — no missing, no extra indices.
* Ensure corrected content preserves the original meaning — only spelling/grammar fixes and English word reconstruction, no paraphrasing.
* For Vietnamese phonetization of English: only reconstruct if the context clearly supports the intended English word.
* If you are not confident about a correction, leave the original text unchanged.
* Ensure the output is clean JSON with no markdown wrapping, no comments, no trailing commas.
"""

