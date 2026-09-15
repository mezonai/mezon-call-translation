"""Object key layout for MinIO/S3. record-service computes this itself
(rather than asking orchestrator) so starting a recording never has a
synchronous dependency on orchestrator being reachable -- PLAN.md keeps
event *reporting* async/best-effort for the same reason.

Same layout convention as the old Filepath.build() in
orchestrator_service/utils/filepath.py. Extension is `.ogg` (PLAN.md
D6-successor): record-service now encodes PCM->OGG/Opus itself on the live
ingest path, so the object this key names *is* the final client-facing
artifact -- there is no separate raw-capture key/derivative-key pair
anymore (audio-processing-service's transcode stage was retired).
"""

from __future__ import annotations

import secrets


def build_object_key(room_id: str, identity: str, source: str) -> str:
    random_suffix = secrets.token_hex(3)
    return f"{room_id}/{identity}-{source}-audio-{random_suffix}.ogg"
