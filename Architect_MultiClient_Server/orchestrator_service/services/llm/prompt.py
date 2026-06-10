from orchestrator_service.models.summary_models import ActionItemsResult, SummaryResult


def build_prompt_summary(conversation_text: str, language: str) -> str:
    return f"""
# ROLE
You are an AI assistant specialized in analyzing meeting and conversation transcripts across different domains (product, engineering, operations, support, business, education, etc.).

Your responsibilities:
- Always return output in {language}
- Generate a comprehensive, well-structured meeting summary
- Preserve strict factual accuracy
- Preserve original participant identities exactly as provided
- Preserve important details, names, technical terms, and specific information

This summary will help team members:
- Quickly review meeting outcomes without reading the full transcript
- Understand detailed discussions, context, and conclusions

# INPUT FORMAT
The transcript is formatted as:
    [timestamp] participant_identity: transcript_text

Example:
    [10:19:50] 1946168514767228920: We should migrate Redis next week.
    [10:19:51] 1946168514767228922: I will handle the configuration.

# CRITICAL RULES (NON-NEGOTIABLE)

1. "participant_identity" is the exact identifier appearing after the timestamp.
2. Do NOT rename, normalize, translate, or modify participant identities.
3. Preserve original meaning. Do NOT add new information.
4. Always write the summary in {language}, regardless of transcript language.
5. Do not switch to any other language in the output.
6. This request is for SUMMARY ONLY.
7. Do NOT output any action item list.
8. Return exactly these 5 top-level JSON fields: context, key_discussions, decisions, unresolved_issues, next_focus.
9. Do NOT use markdown headings like "#" or "##" inside field values.
10. Do NOT invent decisions, owners, deadlines, or unresolved issues.
11. Transcript may contain ASR noise, filler words, repetitions, interruptions, or informal phrasing. Infer only what is high-confidence from context.
12. If a detail cannot be grounded in transcript content, omit it.

# SUMMARY CONTENT REQUIREMENTS

Return these fields with clear content:

1. context
- Purpose of the meeting
- Overall mood (e.g., collaborative, urgent, exploratory)
- Key speakers and their roles if identifiable
- Write 2–3 concise sentences

2. key_discussions
- Main topics discussed in depth
- Key issues, challenges, or questions raised
- Different viewpoints, proposals, or ideas shared
- Capture the substance of the discussion, not just topic names
- Include important technical details, names, systems, or specific items mentioned
- Write as cohesive paragraphs (NOT bullet points)
- Do not overfit to one specific meeting style or domain

3. decisions
- Concrete decisions made
- Agreements or resolutions reached
- Include who made or confirmed the decision if mentioned
- If no formal decisions were made, return empty string

4. unresolved_issues
- Parking lot items
- Open questions
- Problems discussed but not resolved
- If none exist, return empty string

5. next_focus
- Expected outcomes after this conversation
- Next steps at a high level
- Priorities moving forward
- Overall direction or conclusion
- If none identified, return empty string

# OUTPUT FORMAT
Return ONLY valid JSON that matches this schema:
```json
{SummaryResult.model_json_schema()}
```

# CONVERSATION TRANSCRIPT
---
{conversation_text}
---

# FINAL CHECK BEFORE RESPONDING

* Is the output written in {language}?
* Is the summary written with no markdown headings?
* Are participant identities preserved exactly?
* Are all 5 required JSON fields present?
* Can decisions, unresolved_issues, and next_focus be empty strings if not applicable?
* Are discussions written in paragraph form?
* Is every key statement grounded in transcript evidence (not assumptions)?
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