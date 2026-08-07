"""SessionStateRepository adapter: one JSON file per session on local disk.

PLAN.md D5 tier 3 -- this is the only thing that needs to survive a process
crash. S3 multipart upload already durably holds every byte of every part
that finished uploading; this file just remembers (upload_id, parts, status)
so a fresh process can pick the session back up without re-reading any audio.

Known limitation (flagged in PLAN.md D5): if the container's filesystem is
ephemeral and the pod is rescheduled to a different node, this state is
lost along with it. Mitigated by keeping part_size small enough that little
is ever "in flight" only on disk. A persistent volume mount removes the
limitation entirely once available.

No locking in this class, by design (revisited after review -- see note
below): each session_id maps to its own file, so unrelated sessions writing
concurrently never touch the same path. `_write_atomic`'s tmp-file + rename
already makes a single file's write atomic without any lock. The remaining
concern -- two writes to the *same* session's file interleaving -- is
already prevented by callers: every call site that can run concurrently with
another for the same session_id (AppendAudio, StopRecording._finalize) holds
that session's `ActiveSession.lock` around its `save()` call, and by the
time ReportEvent/RecoverOrphanedSessions touch a session it's already been
popped out of the live SessionRegistry, so nothing else can be writing to it
at the same time.

FUTURE: this correctness argument depends on every caller remembering to
hold the per-session lock before calling save() -- it isn't enforced here.
If that ever stops being true (a new call site, a refactor), the intent is
"silently losing whichever concurrent write lost the race (rename is still
atomic, so no corruption) -- not a crash". Two hardenings make that actually
true instead of just documented (production incident 2026-08-06: a shared
fixed `.tmp` name meant the loser's `os.replace()` source had already been
consumed by the winner, raising FileNotFoundError -- which then propagated
out of save() into callers like AppendAudio/StartRecording, and from there
into ingest_server.py's StreamAudio loop, where it got misread as the gRPC
stream itself having broken, dropping otherwise-healthy sessions into the
abrupt-disconnect/grace-period path):
1. `_write_atomic` now uses a per-call-unique tmp name (uuid4 suffix), so two
   concurrent writers never share one intermediate file -- the loser's own
   rename always succeeds, it just gets overwritten a moment later by
   whichever write actually lands last (last-write-wins, matching the
   documented "silently losing a write" intent, no exception either way).
2. `save()` itself never lets a write failure (this race or any other, e.g.
   disk full, permissions) escape as an exception -- logged and swallowed,
   since this file is a crash-recovery convenience (S3 already durably holds
   every uploaded byte), not something any caller's control flow should
   depend on succeeding.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from pathlib import Path

from record_service.domain.models import (
    QualityAnnotation,
    RecordingSession,
    RecordingStatus,
    UploadedPart,
)
from record_service.domain.ports import SessionStateRepository

logger = logging.getLogger(__name__)


class FileSessionStateRepository(SessionStateRepository):
    def __init__(self, directory: str) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, session_id: str) -> Path:
        return self._dir / f"{session_id.replace('/', '_')}.json"

    async def save(self, session: RecordingSession) -> None:
        # Never lets a write failure propagate -- this file is a crash-recovery
        # convenience (PLAN.md D5 tier 3), not something callers' control flow
        # should depend on. A caller that treats a save() failure as "the gRPC
        # stream broke" (ingest_server.py's StreamAudio, historically) would
        # drop an otherwise-healthy session into abrupt-disconnect handling.
        try:
            path = self._path_for(session.session_id)
            payload = json.dumps(_to_dict(session))
            await asyncio.to_thread(self._write_atomic, path, payload)
        except Exception as exc:  # noqa: BLE001 - best-effort persistence boundary
            logger.error("Failed to save session state for %s: %s", session.session_id, exc)

    @staticmethod
    def _write_atomic(path: Path, payload: str) -> None:
        # Per-call-unique tmp name: two concurrent writers for the same
        # session (a documented-but-not-enforced invariant violation, see
        # class docstring) must never share one intermediate file. With a
        # shared name, the loser's os.replace() source would already have
        # been consumed by the winner's rename -- FileNotFoundError. With a
        # unique name, both renames succeed independently; whichever lands
        # last just overwrites `path` a moment later (last-write-wins, no
        # exception either way).
        tmp_path = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            tmp_path.write_text(payload)
            tmp_path.replace(path)  # atomic on POSIX -- never leaves a half-written state file
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    async def delete(self, session_id: str) -> None:
        path = self._path_for(session_id)
        await asyncio.to_thread(path.unlink, True)

    async def list_unfinished(self) -> list[RecordingSession]:
        def _read_all() -> list[RecordingSession]:
            sessions = []
            for file in sorted(self._dir.glob("*.json")):
                try:
                    sessions.append(_from_dict(json.loads(file.read_text())))
                except (json.JSONDecodeError, KeyError, ValueError, FileNotFoundError):
                    # Corrupt/half-written state file, or deleted by a
                    # concurrent delete() between glob() and read_text()
                    # (e.g. a session finishing its report right as this scan
                    # runs) -- skip rather than crash the whole reconciliation
                    # pass over one entry.
                    continue
            return sessions

        return await asyncio.to_thread(_read_all)


def _to_dict(session: RecordingSession) -> dict:
    return {
        "room_id": session.room_id,
        "track_id": session.track_id,
        "participant_identity": session.participant_identity,
        "source": session.source,
        "sample_rate": session.sample_rate,
        "channels": session.channels,
        "bucket": session.bucket,
        "object_key": session.object_key,
        "upload_id": session.upload_id,
        "status": session.status.value,
        "parts": [asdict(p) for p in session.parts],
        "raw_bytes_received": session.raw_bytes_received,
        "frames_received": session.frames_received,
        "dropped_frame_count": session.dropped_frame_count,
        "quality_annotations": [asdict(a) for a in session.quality_annotations],
        "started_at": session.started_at,
        "ended_at": session.ended_at,
        "reported": session.reported,
        "report_attempts": session.report_attempts,
        "last_report_error": session.last_report_error,
    }


def _from_dict(data: dict) -> RecordingSession:
    return RecordingSession(
        room_id=data["room_id"],
        track_id=data["track_id"],
        participant_identity=data["participant_identity"],
        source=data["source"],
        sample_rate=data["sample_rate"],
        channels=data["channels"],
        bucket=data["bucket"],
        object_key=data["object_key"],
        upload_id=data["upload_id"],
        status=RecordingStatus(data["status"]),
        parts=[UploadedPart(**p) for p in data["parts"]],
        raw_bytes_received=data["raw_bytes_received"],
        frames_received=data["frames_received"],
        dropped_frame_count=data["dropped_frame_count"],
        quality_annotations=[QualityAnnotation(**a) for a in data["quality_annotations"]],
        started_at=data["started_at"],
        ended_at=data.get("ended_at"),
        reported=data["reported"],
        report_attempts=data["report_attempts"],
        last_report_error=data.get("last_report_error"),
    )
