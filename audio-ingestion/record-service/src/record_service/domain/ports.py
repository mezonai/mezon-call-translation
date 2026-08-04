"""Ports: interfaces the domain/application layers depend on.

Infra adapters (infra/*) implement these. This is the seam PLAN.md D4 refers
to -- swapping infra/grpc/ingest_server.py for a future infra/sfu adapter
must not require touching anything in domain/ or application/.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from record_service.domain.models import RecordingSession, UploadedPart


class BlobStorage(ABC):
    """Outbound port: durable object storage with multipart-upload semantics."""

    @abstractmethod
    async def create_multipart_upload(self, bucket: str, key: str) -> str:
        """Returns upload_id."""

    @abstractmethod
    async def upload_part(
        self, bucket: str, key: str, upload_id: str, part_number: int, data: bytes
    ) -> str:
        """Returns ETag."""

    @abstractmethod
    async def complete_multipart_upload(
        self, bucket: str, key: str, upload_id: str, parts: Sequence[UploadedPart]
    ) -> None: ...

    @abstractmethod
    async def abort_multipart_upload(self, bucket: str, key: str, upload_id: str) -> None: ...


class SessionStateRepository(ABC):
    """Outbound port: local durable state for crash recovery (PLAN.md D5 tier 3).

    Only (upload_id, key, parts, status) needs to survive a process crash --
    S3 multipart upload itself is the durable store for bytes already uploaded.
    """

    @abstractmethod
    async def save(self, session: RecordingSession) -> None: ...

    @abstractmethod
    async def delete(self, session_id: str) -> None: ...

    @abstractmethod
    async def list_unfinished(self) -> list[RecordingSession]:
        """Sessions left in a non-terminal status by a previous process instance."""


class EventReporter(ABC):
    """Outbound port: report recording lifecycle events to orchestrator.

    Must be safe to call more than once for the same (session, event) --
    the orchestrator endpoint is required to be idempotent (PLAN.md D8/D11).
    """

    @abstractmethod
    async def report(self, session: RecordingSession, event: str) -> bool:
        """Returns True if the orchestrator acknowledged the event."""
