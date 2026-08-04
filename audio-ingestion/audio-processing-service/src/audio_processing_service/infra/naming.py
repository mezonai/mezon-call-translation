"""Derivative object key layout.

audio-ingestion PLAN.md D28 point 2: reuse the same bucket record-service
writes raw captures to, only swap the suffix -- no new bucket/prefix to
provision, and the resulting layout lines up almost exactly with the old
LiveKit-Egress-era storage convention (which also kept the derivative
alongside the raw file under the same room_id prefix).

Raw key layout (record-service/src/record_service/infra/naming.py):
    {room_id}/{identity}-{source}-audio-{random}.pcm
Derivative key: same path, `.pcm` -> `.ogg`.
"""

from __future__ import annotations


def build_derivative_key(raw_object_key: str) -> str:
    if raw_object_key.endswith(".pcm"):
        return raw_object_key[: -len(".pcm")] + ".ogg"
    # Defensive fallback if the raw key ever lacks the expected suffix --
    # still produces a distinct, valid key rather than colliding with the
    # raw object.
    return raw_object_key + ".ogg"
