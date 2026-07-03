from orchestrator_service.models.summary_models import ActionItemsResult, SummaryResult, LightSummaryResult, OverallContextResult


def build_prompt_summary(conversation_text: str, language: str) -> str:
    return f"""
# ROLE & OBJECTIVES
You are a professional Project Manager & Technical Writer. Your task is to convert a meeting transcript into Meeting Minutes that strictly adhere to the provided JSON schema.

# CRITICAL RULES
1. Return ONLY a SINGLE valid JSON object, with absolutely no other text, characters, or markdown wrapping.
2. Always write in {language}.
3. Use only the exact `participant_id` values found in the transcript. Use "[Everyone]" only when a task is explicitly assigned to the entire team.
4. Do not fabricate actions, assignees, or decisions.
5. `next_focus` must contain ONLY CLEAR and EXPLICIT action items. If none are explicitly stated → return [].
6. `detail` must be the most comprehensive section, capturing all critical information.

# INPUT FORMAT
The transcription is a JSON array containing objects with "content", "timestamp", and "participant_id".

# SUMMARY CONTENT REQUIREMENTS

1. **context**: A concise 2-3 sentence summary stating the meeting's purpose and its most important outcome.
2. **key_discussions**: An array of strings. Each item must follow the format "[Topic Title]\n2-4 sentence summary."
3. **next_focus**: An array of clear action items. If there are none, return [].
4. **detail**: 
   - The MOST DETAILED section of the document.
   - Each element should be a bullet point grouped by logical topics.
   - Priority structure: Who + Content + Decision + Technical details + Timestamp.
   - Each bullet point can be 1-4 lines long.

# OUTPUT FORMAT (MANDATORY)
Return only a valid JSON object matching the SummaryResult schema:
```json
{SummaryResult.model_json_schema()}
```

# FEW-SHOT EXAMPLES

**Example 1:**
```json
{{
  "context": "Cuộc họp nhằm kiểm tra tiến độ Pull Request và Task, đồng thời thảo luận về cải thiện UI/UX.",
  "key_discussions": [
    "[Tiến độ Pull Request và Task]\\nĐã yêu cầu review khoảng 10 Pull Request. Một số task đã hoàn thành, một số bị tạm hoãn.",
    "[Xác nhận hoàn thành Task]\\nNhiều task đã được giải quyết và được phê duyệt."
  ],
  "next_focus": [
    "[1779509763680243712] Review 10 Pull Request.",
    "[1779484387973271552] Tổng hợp ý kiến cải tiến UX và gửi cho [1779509763680243712]."
  ],
  "detail": [
    "Yêu cầu review Pull Request: [1779484387973271552] đã chia sẻ màn hình và yêu cầu review khoảng 10 Pull Request (00:04:15).",
    "Xác nhận hoàn thành Task: [1779484387973271552] báo cáo Task 52 và Task 10073 dự kiến hoàn thành (00:03:10).",
    "Migration Lobby: Team thống nhất migrate từng bước từ V1 sang V2 (10:17:00)."
  ]
}}
```

**Example 2:**
```json
{{
  "context": "Cuộc họp kỹ thuật xử lý sự cố hệ thống thanh toán trên Production.",
  "key_discussions": [
    "[Nguyên nhân gốc rễ]\\nLỗi do lệnh KEYS * trên Redis trong Middleware.",
    "[Giải pháp]\\nChuyển sang Redis Set/Hash và triển khai Blue-Green Deployment."
  ],
  "next_focus": [
    "[Backend Son] Viết tài liệu Post-mortem.",
    "[DevOps Long] Giám sát hệ thống sau triển khai."
  ],
  "detail": [
    "Xác nhận tình trạng lỗi: Hệ thống xuất hiện lỗi Connection Timeout (09:00:05).",
    "Xác định vị trí lỗi: Backend Son chỉ ra request bị timeout ở tầng Middleware (09:02:42).",
    "Giải pháp kỹ thuật: Team thống nhất thay thế KEYS * bằng SISMEMBER O(1) (09:08:02)."
  ]
}}
```

# IMPORTANT EXECUTION INSTRUCTION
Strictly process the transcription below. Start directly with the JSON object.

# DATA TO BE PROCESSED
---
{conversation_text}
---
"""



def build_light_summary_prompt(
    conversation_str: str,
    previous_context: str = "",
    language: str = "Vietnamese",
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
8. Do not return information about incomplete tails or later topics.

# SUMMARY RULES
1. ONLY return a single valid JSON object, absolutely no other text, characters, or markdown wrapping.
2. Always write in {language}.
3. Use exactly the `participant_id` values found in the transcript. Only use "[Everyone]" when a task is explicitly assigned to the entire team.
4. Do not invent actions, assignees, or decisions.
5. `next_focus` MUST ONLY contain CLEAR and SPECIFIC action items. If none are explicitly stated → return [].
6. `detail` must be the most comprehensive part, capturing all important information.
7. Use PREVIOUS CONTEXT to understand the flow, but only summarize the completed section within the current input.

# SUMMARY CONTENT REQUIREMENTS
1. **context**: A concise 1-3 sentence summary stating the purpose of the meeting and its most important outcome.
2. **key_discussions**: An array of strings. Each item must follow the format "[Topic Title]\n1-3 sentence summary."
3. **next_focus**: An array of clear action items. If none, return [].
4. **detail**:
* The MOST DETAILED part of the document.
* Each element should be a bullet point grouped by logical topics.
* Preferred structure: Who + Content + Decision + Technical details + Timestamp.
* Each bullet point can be 1-4 lines long.

# OUTPUT FORMAT
Only return a valid JSON object according to the schema:
```json
{LightSummaryResult.model_json_schema()}
```

# FEW-SHOT EXAMPLES

## Example 1 - Complete topic, later topic starts after boundary
```json
{{
  "end_message_time": "00:12:45",
  "context": "The section focuses on the Pull Request Review and the status of related tasks.",
  "key_discussions": [
    "[Review Pull Request]\nThe team discussed the number of PRs to review, completed tasks, and blockers to be addressed next."
  ],
  "next_focus": [
    "[1779509763680243712] Review the remaining Pull Requests."
  ],
  "detail": [
    "[1779484387973271552] requested a PR review and shared task status (00:04:15).",
    "The team agreed to continue reviewing the remaining PRs before moving on to the deployment topic (00:12:45)."
  ]
}}
```

## Example 2 - First topic is not complete
```json
{{
  "end_message_time": null,
  "context": "",
  "key_discussions": [],
  "next_focus": [],
  "detail": []
}}
```

## Example 3 - Do not over-split small updates
```json
{{
  "end_message_time": "00:28:00",
  "context": "The section summarizes sprint progress, UI bugs, and testing blockers.",
  "key_discussions": [
    "[Sprint Progress]\nThe team updated multiple minor tasks related to UI, bug fixes, and testing within the same workflow."
  ],
  "next_focus": [],
  "detail": [
    "Participants took turns updating task status, UI bugs, and testing notes within the same discussion thread (00:20:00-00:28:00)."
  ]
}}
```

# EXECUTION
Analyze the input transcript and only return the JSON object.
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
