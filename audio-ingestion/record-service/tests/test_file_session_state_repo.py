"""Round-trip test for the actual infra adapter (not a fake) -- this is the
piece PLAN.md D5 tier 3 crash-recovery depends on, so it needs to prove it
survives a real serialize/deserialize cycle, including enums and nested
dataclasses.
"""

from __future__ import annotations

import tempfile

from record_service.domain.models import (
    QualityAnnotation,
    RecordingSession,
    RecordingStatus,
    UploadedPart,
)
from record_service.infra.state.file_session_state_repo import FileSessionStateRepository


async def test_save_and_reload_round_trips_all_fields():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = FileSessionStateRepository(tmp_dir)

        session = RecordingSession(
            room_id="room-x",
            track_id="track-x",
            participant_identity="alice",
            source="mic",
            sample_rate=16000,
            channels=1,
            bucket="rec",
            object_key="room-x/alice-mic-audio-abc.pcm",
            upload_id="upload-1",
            status=RecordingStatus.GRACE_WAIT,
            parts=[UploadedPart(part_number=1, etag="etag-1")],
            raw_bytes_received=1234,
            frames_received=10,
            dropped_frame_count=2,
            quality_annotations=[QualityAnnotation(start_offset_ms=100, reason="high_drop_rate")],
        )
        session.ended_at = session.started_at + 5.0

        await repo.save(session)
        [reloaded] = await repo.list_unfinished()

        assert reloaded.session_id == session.session_id
        assert reloaded.status == RecordingStatus.GRACE_WAIT
        assert reloaded.upload_id == "upload-1"
        assert reloaded.parts == [UploadedPart(part_number=1, etag="etag-1")]
        assert reloaded.raw_bytes_received == 1234
        assert reloaded.quality_annotations[0].reason == "high_drop_rate"
        assert reloaded.ended_at == session.ended_at


async def test_delete_removes_state_file():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = FileSessionStateRepository(tmp_dir)
        session = RecordingSession(
            room_id="room-y",
            track_id="track-y",
            participant_identity="bob",
            source="mic",
            sample_rate=16000,
            channels=1,
            bucket="rec",
            object_key="room-y/bob-mic-audio-def.pcm",
        )
        await repo.save(session)
        assert len(await repo.list_unfinished()) == 1

        await repo.delete(session.session_id)
        assert await repo.list_unfinished() == []


async def test_corrupt_state_file_is_skipped_not_fatal():
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo = FileSessionStateRepository(tmp_dir)
        bad_file = repo._path_for("room-z:track-z")
        bad_file.write_text("{not valid json")

        assert await repo.list_unfinished() == []  # skipped, doesn't raise
