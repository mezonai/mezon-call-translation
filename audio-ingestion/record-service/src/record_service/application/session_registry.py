"""In-memory registry of active recording sessions.

Not in the original PLAN.md skeleton sketch -- added because the multipart
upload buffer and the grace-period timer are orchestration state, not pure
domain state (domain/models.py) nor an infra concern. Lives in the
application layer since it's only ever touched by the use cases below.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from record_service.domain.models import RecordingSession


class ActiveSession:
    __slots__ = ("session", "buffer", "lock", "grace_task")

    def __init__(self, session: RecordingSession) -> None:
        self.session = session
        self.buffer = bytearray()
        self.lock = asyncio.Lock()
        self.grace_task: asyncio.Task | None = None


class _RefCountedLock:
    __slots__ = ("lock", "refcount")

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.refcount = 0


class SessionRegistry:
    """Keyed by RecordingSession.session_id (room_id:track_id, PLAN.md D3)."""

    def __init__(self) -> None:
        self._sessions: dict[str, ActiveSession] = {}
        # Per-session_id locks for StartRecording's create-or-resume decision
        # (start_recording.py). Deliberately scoped to session_id, not a
        # single global lock -- starting a session for one track must never
        # block starting an unrelated one on a different track/room while
        # create_multipart_upload is in flight.
        self._creation_locks: dict[str, _RefCountedLock] = {}

    def get(self, session_id: str) -> ActiveSession | None:
        return self._sessions.get(session_id)

    def put(self, active: ActiveSession) -> None:
        self._sessions[active.session.session_id] = active

    def pop(self, session_id: str) -> ActiveSession | None:
        return self._sessions.pop(session_id, None)

    def values(self) -> list[ActiveSession]:
        return list(self._sessions.values())

    @contextlib.asynccontextmanager
    async def creation_lock(self, session_id: str) -> AsyncIterator[None]:
        """Serializes the create-or-resume decision for one session_id.

        `_creation_locks` is refcounted and self-evicting: entries are
        created on first use and deleted once no task holds or is waiting on
        them, so this dict doesn't grow without bound over a long-running
        process's lifetime (every new track publish is a fresh session_id
        that would otherwise leak one entry forever). The get-or-create and
        the refcount increment/decrement below never straddle an `await`, so
        they're atomic under asyncio's single-threaded cooperative scheduling
        without needing a lock of their own.
        """
        entry = self._creation_locks.get(session_id)
        if entry is None:
            entry = _RefCountedLock()
            self._creation_locks[session_id] = entry
        entry.refcount += 1
        try:
            async with entry.lock:
                yield
        finally:
            entry.refcount -= 1
            if entry.refcount == 0:
                self._creation_locks.pop(session_id, None)
