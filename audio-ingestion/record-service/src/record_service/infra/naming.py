"""Object key layout for MinIO/S3. record-service computes this itself
(rather than asking orchestrator) so starting a recording never has a
synchronous dependency on orchestrator being reachable -- PLAN.md keeps
event *reporting* async/best-effort for the same reason.

Same layout convention as the old Filepath.build() in
orchestrator_service/utils/filepath.py, minus the container extension
(PLAN.md D6: raw PCM, no encode -> `.pcm`, not `.ogg`).
"""

from __future__ import annotations

import secrets


def build_object_key(room_id: str, identity: str, source: str) -> str:
    random_suffix = secrets.token_hex(3)
    return f"{room_id}/{identity}-{source}-audio-{random_suffix}.pcm"
