from orchestrator_service.models.summary_models import (
    LightSummaryResult,
    OverallContextResult,
    SummaryResult,
)


def build_prompt_summary(conversation_text: str, language: str) -> str:
    return f"""
# ROLE & OBJECTIVES
You are a professional Project Manager & Technical Writer. Your task is to convert a meeting transcript into Meeting Minutes that strictly adhere to the provided JSON schema.

# SUMMARY RULES
1. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.
2. Always write in the following language: {language}.
3. Participant Naming:
* In next_focus: Use raw [participant_id] (e.g., [hoang.dohuy]). Only use "[Everyone]" when a task is explicitly assigned to the entire team, or "[Unknown]" when it is impossible to clearly identify who the task is assigned to.
* In narrative text (context, key_discussions, detail):
  - Identity source: Every speaker and actor MUST be named strictly using the firstname extracted from BEFORE the dot in participant_id (e.g., "alex.chen" → "Alex", "hoang.dohuy" → "Hoang" / "Hoàng").
  - Spoken text override: Spoken transcript content may contain ASR errors, nicknames, or middle/last names. NEVER use a name from the spoken text if its base spelling differs from the firstname before the dot.
  - Accent / diacritics: In languages using accents or diacritics (such as Vietnamese), you may use the accented form (e.g., "Hoàng") ONLY IF its base ASCII spelling strictly matches the extracted firstname ("hoang"). Otherwise, use the capitalized ASCII firstname as-is (e.g., "Alex", "Hoang").
  - NEVER use any part after the dot as a person's name.
  - NEVER put raw participant_id with dots (e.g., hoang.dohuy) inside narrative sentences.
4. Do not invent actions, assignees, or decisions.
5. The `next_focus` field must be the most important part that contains CLEAR and SPECIFIC action items.
6. The `detail` field must be the most comprehensive part, capturing all important information.

# INPUT FORMAT
* The input transcript covers the ENTIRE meeting room conversation.
* The transcript is provided as a list of messages, where each message contains: `content`, `timestamp`, and `participant_id`.
* Note on `participant_id`: Each participant_id follows the format `firstname.lastnamemiddlename` (e.g., `alex.chen`, `hoang.dohuy`). The substring BEFORE the dot is the ONLY authoritative firstname for that participant. Ignore any conflicting names spoken in the transcript.

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
* Re-verify participant names:
  - `next_focus`: MUST use raw `[participant_id]` (e.g., `[hoang.dohuy]`, `[alex.chen]`).
  - Narrative fields (`context`, `key_discussions`, `detail`): MUST use the firstname derived strictly from BEFORE the dot of `participant_id` (e.g., "Hoàng"/"Hoang", "Alex"). NEVER use names from spoken dialogue that differ from the firstname before the dot.
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
* Note on `participant_id`: Each participant_id follows the format `firstname.lastnamemiddlename` (e.g., `alex.chen`, `hoang.dohuy`). The substring BEFORE the dot is the ONLY authoritative firstname for that participant. Ignore any conflicting names spoken in the transcript.

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
* In narrative text (context, key_discussions, detail):
  - Identity source: Every speaker and actor MUST be named strictly using the firstname extracted from BEFORE the dot in participant_id (e.g., "alex.chen" → "Alex", "hoang.dohuy" → "Hoang" / "Hoàng").
  - Spoken text override: Spoken transcript content may contain ASR errors, nicknames, or middle/last names. NEVER use a name from the spoken text if its base spelling differs from the firstname before the dot.
  - Accent / diacritics: In languages using accents or diacritics (such as Vietnamese), you may use the accented form (e.g., "Hoàng") ONLY IF its base ASCII spelling strictly matches the extracted firstname ("hoang"). Otherwise, use the capitalized ASCII firstname as-is (e.g., "Alex", "Hoang").
  - NEVER use any part after the dot as a person's name.
  - NEVER put raw participant_id with dots (e.g., hoang.dohuy) inside narrative sentences.
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
* Re-verify participant names:
  - `next_focus`: MUST use raw `[participant_id]` (e.g., `[hoang.dohuy]`, `[alex.chen]`).
  - Narrative fields (`context`, `key_discussions`, `detail`): MUST use the firstname derived strictly from BEFORE the dot of `participant_id` (e.g., "Hoàng"/"Hoang", "Alex"). NEVER use names from spoken dialogue that differ from the firstname before the dot.
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
