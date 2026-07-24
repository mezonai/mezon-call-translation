from orchestrator_service.models.summary_models import (
    ActionItemsResult,
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
3. Use exactly the participant_id values found in the transcript (e.g., [user-1], [user-2]). Only use "[Everyone]" when a task is explicitly assigned to the entire team, or "[Unknown]" when it is impossible to clearly identify who the task is assigned to.
4. Do not invent actions, assignees, or decisions.
5. The `next_focus` field must be the most important part that contains CLEAR and SPECIFIC action items.
6. The `detail` field must be the most comprehensive part, capturing all important information.

# INPUT FORMAT
* The input transcript covers the ENTIRE meeting room conversation.
* The transcript is provided as a list of messages, where each message contains: `content`, `timestamp`, and `participant_id`.

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
* The structure must strictly follow this format: "[participant_id] Task content. Deadline (if any) and (timestamp)." (Example: "[user-1] Check the server logs (09:18:51)")
4. **detail**:
* This is the MOST DETAILED part of the document.
* Each element should be a bullet point grouped by logical topics.
* Preferred structure: Who + Content + Decision + Technical details + Timestamp.
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
    "[user-1] Phối hợp với Morgan để release task 3094 và insert record lên production (09:18:51).",
    "[user-1] Fix lỗi search trên cloud cho task 3103 của Confident Project (09:18:51).",
    "[user-2] Thiết kế UI bằng storyboard cho tính năng Guest User Invitation (09:31:20)."
  ],
  "detail": [
    "[user-1] báo cáo task 3094 và ES-Link đã hoàn thành, sẵn sàng release và chuẩn bị insert data trên Prod (09:18:51).",
    "[user-1] đang xử lý task 3103 do gặp lỗi search trên cloud dù chạy local bình thường (09:18:51).",
    "[user-3] chỉ đạo dev test kỹ trên Staging và chủ động pin release cuốn chiếu các task nhỏ, ít impact (09:20:56).",
    "[user-2] đã đọc spec Guest User Invitation và sẽ vẽ storyboard UI flow để trực quan hóa cho team review (09:31:20)."
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
    "[user-1] báo cáo tỷ lệ tài khoản bị ban đã giảm rõ rệt sau khi tạm dừng tính năng Massadei từ ngày hôm qua (09:49:51).",
    "[user-2] xác nhận hệ thống Notification trên Staging chạy ổn định và không phát sinh lỗi mới (09:50:38)."
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
    "[user-1] Create a sub-ticket to audit GCP infrastructure security recommendations by 17:00 Oct 25th (09:34:31).",
    "[Everyone] Review and leave feedback on the new Vector DB architecture draft on Notion before Friday (09:40:15).",
    "[Unknown] Sign up for a 3rd-party code scanning service to test integration into the CI/CD pipeline (09:32:56)."
  ],
  "detail": [
    "[user-1] proposed automated security scans on every code commit to detect outdated packages (09:32:10).",
    "[user-2] noted the need to audit open ports and excessive IAM permissions on GCP (09:34:31).",
    "The team agreed that all members must review the new Vector Store architecture document on Notion prior to next week's sync (09:40:15)."
  ]
}}

# EXECUTION
Analyze the input transcript and only return the JSON object.

# FINAL CHECK
* Check if the content is clean and standardized, and ensure it does not contain any comment artifacts or strange characters generated during the response process like: "//", "//comment ", "] ", "[ ",....
* Re-verify all `participant_id` values to ensure they are ABSOLUTELY ACCURATE.
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
3. Use exactly the participant_id values found in the transcript (e.g., [user-1], [user-2]). Only use "[Everyone]" when a task is explicitly assigned to the entire team, or "[Unknown]" when it is impossible to clearly identify who the task is assigned to.
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
* The structure must strictly follow this format: "[participant_id] Task content. Deadline (if any) and (timestamp)." (Example: "[user-1] Check the server logs (09:18:51)")
4. **detail**:
* This is the MOST DETAILED part of the document.
* Each element should be a bullet point grouped by logical topics.
* Preferred structure: Who + Content + Decision + Technical details + Timestamp.
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
    "[user-1] Phối hợp với Morgan để release task 3094 và insert record lên production (09:18:51).",
    "[user-1] Fix lỗi search trên cloud cho task 3103 của Confident Project (09:18:51)."
  ],
  "detail": [
    "[user-1] báo cáo task 3094 đã hoàn thành và sẵn sàng cho đợt release tiếp theo (09:18:51).",
    "[user-1] đang xử lý task 3103 do gặp lỗi search trên cloud dù local hoạt động bình thường (09:18:51).",
    "[user-2] đề xuất dev chủ động release các task nhỏ, rủi ro thấp để dễ kiểm soát lỗi (09:20:56).",
    "[user-3] giải thích hai luồng release hiện tại và thống nhất phương án cho các task độc lập (09:22:37)."
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
    "[user-1] Complete the Filter UI design for the Game Manager (09:26:01).",
    "[user-2] Share screenshots of the guest invitation UI design with management for review (09:30:54)."
  ],
  "detail": [
    "[user-1] reported that security documents were sent and is currently working on the Filter UI (09:26:01).",
    "[user-3] advised caution during deployments due to upcoming client holiday schedules (09:28:15).",
    "[user-2] proposed sending direct invitation links to guest users to avoid email setup complexity (09:29:06).",
    "[user-3] requested [user-2] to share screenshots of the proposed invitation UI for review (09:30:54)."
  ]
}}

# EXECUTION
Analyze the input transcript and only return the JSON object.

# FINAL CHECK
* Check if the content is clean and standardized, and ensure it does not contain any comment artifacts or strange characters generated during the response process like: "//", "//comment ", "] ", "[ ",....
* Re-verify all `participant_id` values to ensure they are ABSOLUTELY ACCURATE.
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


def build_prompt_action_items(conversation_text: str, language: str) -> str:
    return f"""
# ROLE
You are an AI assistant specialized in extracting action items from meeting and conversation transcripts.

Your task uses a TWO-STAGE approach:
1) Identify actionable tasks first (task-centric extraction)
2) Then determine task ownership and deadlines

# INPUT FORMAT
Transcript format:
[timestamp] participant_identity: transcript_text

# CRITICAL RULES (NON-NEGOTIABLE)

1. "participant_identity" is the EXACT identifier after the timestamp.
2. Do NOT rename, normalize, translate, or modify participant identities.
3. Preserve original meaning. Do NOT add new information.
4. Extract ONLY real actionable tasks (not ideas, questions, or general discussion).
5. Always write tasks in {language}, regardless of transcript language.
6. If no valid tasks exist, return an empty list.
7. Do NOT output meeting summaries.

---

# EXTRACTION STRATEGY (STRICT TWO-STAGE PROCESS)

## STAGE 1 — TASK IDENTIFICATION (OWNER-AGNOSTIC)

First, scan the transcript and extract ALL actionable tasks WITHOUT considering ownership.

A task is valid if:
- It describes a concrete action that can be executed and tracked
- It refers to a real deliverable, change, fix, implementation, or follow-up
- It is discussed as something that needs to be done

At this stage:
- Ignore who will do it
- Ignore priority and deadline
- Focus only on WHAT needs to be done

Do NOT extract:
- Questions
- Brainstorming ideas
- Opinions or explanations
- Status updates without actionable work

---

## STAGE 2 — OWNERSHIP & DEADLINE MAPPING

For EACH identified task:

### A. OWNER ASSIGNMENT

Determine the responsible participant_identity using evidence:

1. Direct self-commitment:
   - "I will...", "I'll handle...", "Em làm...", "Để em xử lý..."

2. Assignment + acceptance in nearby dialogue:
   - A assigns task to B
   - B confirms: "ok", "vâng", "được", "got it", "em xử lý"

3. First-person ownership in natural speech:
   - "em fix rồi gửi PR"
   - "hôm nay em làm phần này"

If ownership is:
- Clearly identifiable → assign exact participant_identity
- Ambiguous or missing → set owner as: "unknown"

### B. DEADLINE DETECTION

If a deadline or time reference is explicitly mentioned for that task:
Append to task:
(Deadline: exact wording from transcript)

Rules:
- Must be explicitly stated
- Must relate to the same task
- Keep original wording
- Do NOT convert to calendar dates

---

# ACTION ITEM STRUCTURE (STRICT)

Each action item must include:

OWNER → participant_identity OR "unknown"
TASK → clear, executable action description

Task description:
- Concise but complete sentence
- Preserve technical terms, names, systems, features
- Concrete and trackable

If deadline exists:
Append at end of task description.

---

# OUTPUT FORMAT
Return ONLY valid JSON matching this schema:
```json
{ActionItemsResult.model_json_schema()}
```
---
TRANSCRIPT
{conversation_text}
---
FINAL CHECK

Were tasks extracted BEFORE assigning owners?

Does every task have an owner or "unknown"?

Are participant identities exact when assigned?

Are deadlines included only if explicit?

Are all tasks actionable and trackable?

Is the output written in {language}?
"""


def build_simple_prompt_action_items(conversation_text: str, language: str) -> str:
    return f"""
# ROLE
You are an AI assistant specialized in extracting action items from meeting transcripts.

# INPUT FORMAT
Transcript format: [timestamp] participant_identity: transcript_text

# CRITICAL RULES (NON-NEGOTIABLE)
1. "participant_identity" must be the EXACT identifier from the transcript. Do not modify, translate, or normalize it.
2. Extract ONLY concrete, real actionable tasks (deliverables, fixes, implementations, follow-ups). Do NOT extract questions, brainstorming ideas, or status updates.
3. Write all task descriptions in {language}.
4. If a deadline or time reference is explicitly mentioned for a task, append it to the end of the task description in the format: "(Deadline: [exact wording])". Do not convert to calendar dates.
5. If a task has no clear owner or ownership is ambiguous, set participant_identity as "unknown".
6. If no valid tasks exist, return an empty list.

# OUTPUT INSTRUCTION
- You must perform the task-centric extraction and ownership mapping internally.
- Do NOT output any introduction, explanation, stage breakdown, or final checklist.
- Respond ONLY with a single, valid JSON object matching the schema below.

# JSON SCHEMA
{ActionItemsResult.model_json_schema()}

---
TRANSCRIPT:
{conversation_text}
---
"""
