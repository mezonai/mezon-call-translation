"""Exercises the application-layer use cases end to end against the fakes in
fakes.py -- this is what actually proves the PLAN.md D5/D11/D12 recovery
design behaves as designed, not just that the modules import cleanly.
"""

from __future__ import annotations

import asyncio

import pytest

from record_service.application.append_audio import AppendAudio
from record_service.application.recover_orphaned_sessions import RecoverOrphanedSessions
from record_service.application.report_event import ReportEvent
from record_service.application.session_registry import SessionRegistry
from record_service.application.start_recording import StartRecording
from record_service.application.stop_recording import StopRecording
from record_service.domain.models import RecordingStatus
from record_service.domain.policies import RecordingPolicy, RetryPolicy
from tests.fakes import (
    FakeBlobStorage,
    FakeEventReporter,
    FakeSessionStateRepository,
    FakeStreamEncoderFactory,
)


def _wire(
    policy: RecordingPolicy | None = None,
    event_reporter: FakeEventReporter | None = None,
    encoder_factory: FakeStreamEncoderFactory | None = None,
):
    policy = policy or RecordingPolicy(part_size_bytes=10, grace_period_seconds=0.1)
    registry = SessionRegistry()
    blob_storage = FakeBlobStorage()
    state_repo = FakeSessionStateRepository()
    event_reporter = event_reporter or FakeEventReporter()
    encoder_factory = encoder_factory or FakeStreamEncoderFactory()

    report_event = ReportEvent(event_reporter, state_repo, policy.report_retry)
    start = StartRecording(registry, blob_storage, state_repo, report_event, encoder_factory)
    append = AppendAudio(registry, blob_storage, state_repo, policy, encoder_factory)
    stop = StopRecording(registry, blob_storage, state_repo, report_event, policy)
    reconcile = RecoverOrphanedSessions(registry, blob_storage, state_repo, report_event, policy)

    return registry, blob_storage, state_repo, event_reporter, start, append, stop, reconcile, policy


async def test_normal_flow_completes_and_reports():
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, _ = _wire()

    session = await start.execute(
        room_id="room-1",
        track_id="track-1",
        participant_identity="alice",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-1/alice-mic-audio-abc123.pcm",
    )
    await asyncio.sleep(0)  # let the fire-and-forget recording.started report run

    ok = await append.execute(session.session_id, b"x" * 25)  # 2 full 10-byte parts + 5 remainder
    assert ok

    await stop.execute(session.session_id, graceful=True)

    assert blob_storage.uploads[session.upload_id]["completed"] is True
    assert blob_storage.uploaded_bytes(session.upload_id) == b"x" * 25
    assert event_reporter.events == [
        (session.session_id, "recording.started"),
        (session.session_id, "recording.completed"),
    ]
    assert state_repo.store == {}  # deleted once successfully reported


async def test_abrupt_disconnect_then_reconnect_within_grace_resumes_same_upload():
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, policy = _wire(
        RecordingPolicy(part_size_bytes=1000, grace_period_seconds=5.0)
    )

    session = await start.execute(
        room_id="room-2",
        track_id="track-2",
        participant_identity="bob",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-2/bob-mic-audio-def456.pcm",
    )
    upload_id = session.upload_id
    await asyncio.sleep(0)  # let the fire-and-forget recording.started report run
    await append.execute(session.session_id, b"before-blip")

    await stop.execute(session.session_id, graceful=False)  # abrupt -> GRACE_WAIT

    resumed = await start.execute(
        room_id="room-2",
        track_id="track-2",
        participant_identity="bob",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="should-be-ignored.pcm",
    )
    assert resumed.upload_id == upload_id  # same session, not a new multipart upload
    assert resumed.status == RecordingStatus.RECORDING

    await append.execute(session.session_id, b"after-reconnect")
    await stop.execute(session.session_id, graceful=True)

    assert blob_storage.uploaded_bytes(upload_id) == b"before-blipafter-reconnect"
    # Only 1 "recording.started" -- the resume within grace period reuses the
    # same session_id/upload rather than starting a new one, so it doesn't
    # report a second "started" event.
    assert event_reporter.events == [
        (session.session_id, "recording.started"),
        (session.session_id, "recording.completed"),
    ]


async def test_grace_period_timeout_finalizes_best_effort():
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, policy = _wire(
        RecordingPolicy(part_size_bytes=1000, grace_period_seconds=0.05)
    )

    session = await start.execute(
        room_id="room-3",
        track_id="track-3",
        participant_identity="carol",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-3/carol-mic-audio-ghi789.pcm",
    )
    await append.execute(session.session_id, b"only-this-much")
    await stop.execute(session.session_id, graceful=False)

    await asyncio.sleep(0.2)  # let the grace timer expire

    assert blob_storage.uploads[session.upload_id]["completed"] is True
    assert blob_storage.uploaded_bytes(session.upload_id) == b"only-this-much"
    assert event_reporter.events == [
        (session.session_id, "recording.started"),
        (session.session_id, "recording.completed"),
    ]


async def test_drop_rate_annotation_never_discards_data():
    policy = RecordingPolicy(part_size_bytes=1000, drop_rate_warning_threshold=0.2)
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, _ = _wire(policy)

    session = await start.execute(
        room_id="room-4",
        track_id="track-4",
        participant_identity="dave",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-4/dave-mic-audio-jkl012.pcm",
    )

    await append.execute(session.session_id, b"real-audio-bytes")
    # Simulate the agent reporting a burst of locally-dropped frames (D12) --
    # this must only annotate, never abort/replace the session.
    await append.execute(session.session_id, None, dropped_count=10)

    assert session.drop_rate() > policy.drop_rate_warning_threshold
    assert len(session.quality_annotations) == 1
    assert session.quality_annotations[0].reason == "high_drop_rate"
    assert session.status == RecordingStatus.RECORDING  # not discarded, not restarted

    await stop.execute(session.session_id, graceful=True)
    assert blob_storage.uploaded_bytes(session.upload_id) == b"real-audio-bytes"


async def test_low_byte_rate_flags_quality_annotation_but_still_completes():
    policy = RecordingPolicy(part_size_bytes=1000, byte_rate_tolerance=0.5)
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, _ = _wire(policy)

    session = await start.execute(
        room_id="room-5",
        track_id="track-5",
        participant_identity="erin",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-5/erin-mic-audio-mno345.pcm",
    )
    # Backdate started_at to simulate a long session that received almost no bytes.
    session.started_at -= 10.0
    await append.execute(session.session_id, b"tiny")

    await stop.execute(session.session_id, graceful=True)

    assert session.status == RecordingStatus.COMPLETED  # D11: flagged, not failed
    assert any(a.reason == "low_byte_rate" for a in session.quality_annotations)


async def test_dead_encoder_is_restarted_and_annotated_not_fatal():
    """PLAN.md D6-successor: if a session's encoder dies mid-stream, the
    session must not be dropped -- a fresh encoder starts transparently and
    the restart is annotated (D12 style), same philosophy as
    test_drop_rate_annotation_never_discards_data above."""
    encoder_factory = FakeStreamEncoderFactory(dead_on_arrival_count=1)
    _, blob_storage, state_repo, event_reporter, start, append, stop, _, _ = _wire(
        encoder_factory=encoder_factory
    )

    session = await start.execute(
        room_id="room-10",
        track_id="track-10",
        participant_identity="judy",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-10/judy-mic-audio-abc999.ogg",
    )
    # The encoder created by start_recording.py above is dead on arrival
    # (dead_on_arrival_count=1) -- the first append call must detect that,
    # start a replacement, and still deliver the audio through it.
    ok = await append.execute(session.session_id, b"still-delivered")
    assert ok

    assert len(encoder_factory.created) == 2  # the DOA one + its replacement
    assert any(a.reason == "encoder_restarted" for a in session.quality_annotations)

    await stop.execute(session.session_id, graceful=True)
    assert blob_storage.uploaded_bytes(session.upload_id) == b"still-delivered"
    assert session.status == RecordingStatus.COMPLETED


async def test_reconciler_finalizes_session_orphaned_by_a_crash():
    """Simulates a process crash: a session sits in durable state with
    status=RECORDING (never got a graceful/abrupt stop call because the
    whole process died) and some parts already uploaded before the crash.
    PLAN.md D5 tier 3.
    """
    policy = RecordingPolicy(part_size_bytes=10)
    blob_storage = FakeBlobStorage()
    state_repo = FakeSessionStateRepository()
    event_reporter = FakeEventReporter()
    report_event = ReportEvent(event_reporter, state_repo, policy.report_retry)
    # A fresh, empty registry -- this stands in for the new process's
    # in-memory state after a restart, deliberately NOT the registry used
    # below to set up the orphan (that one belonged to the "crashed" instance
    # and is gone).
    reconcile = RecoverOrphanedSessions(SessionRegistry(), blob_storage, state_repo, report_event, policy)

    start = StartRecording(SessionRegistry(), blob_storage, state_repo, report_event, FakeStreamEncoderFactory())
    orphan = await start.execute(
        room_id="room-6",
        track_id="track-6",
        participant_identity="frank",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-6/frank-mic-audio-pqr678.pcm",
    )
    await asyncio.sleep(0)  # let the fire-and-forget recording.started report run
    from record_service.domain.models import UploadedPart

    etag = await blob_storage.upload_part(
        "rec", orphan.object_key, orphan.upload_id, 1, b"partial-before-crash"
    )
    orphan.parts.append(UploadedPart(part_number=1, etag=etag))
    await state_repo.save(orphan)
    # No stop_recording call at all -- this is the crash.

    remaining = await reconcile.execute()

    assert remaining == 0
    assert blob_storage.uploads[orphan.upload_id]["completed"] is True
    assert event_reporter.events == [
        (orphan.session_id, "recording.started"),
        (orphan.session_id, "recording.completed"),
    ]
    assert state_repo.store == {}


async def test_reconciler_retries_reporting_until_orchestrator_recovers():
    """A terminal, already-uploaded session whose event delivery kept
    failing (orchestrator down) must not be lost -- the reconciler keeps
    retrying it on each pass instead of it silently vanishing (D8).
    """
    policy = RecordingPolicy(part_size_bytes=10, report_retry=RetryPolicy(max_attempts=1))
    blob_storage = FakeBlobStorage()
    state_repo = FakeSessionStateRepository()
    flaky_reporter = FakeEventReporter(fail_times=1)

    # Separate, unrelated reporter for the fire-and-forget "recording.started"
    # report -- this test's flaky_reporter/fail_times budget is calibrated
    # for the reconciler's terminal-event report below, not this one.
    start_report_event = ReportEvent(FakeEventReporter(), state_repo, policy.report_retry)
    start = StartRecording(
        SessionRegistry(), blob_storage, state_repo, start_report_event, FakeStreamEncoderFactory()
    )
    session = await start.execute(
        room_id="room-7",
        track_id="track-7",
        participant_identity="grace",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-7/grace-mic-audio-stu901.pcm",
    )
    await asyncio.sleep(0)  # let the fire-and-forget recording.started report run
    from record_service.domain.models import UploadedPart

    etag = await blob_storage.upload_part("rec", session.object_key, session.upload_id, 1, b"data")
    session.parts.append(UploadedPart(part_number=1, etag=etag))
    session.status = RecordingStatus.COMPLETED
    session.ended_at = session.started_at + 1
    await state_repo.save(session)

    report_event = ReportEvent(flaky_reporter, state_repo, policy.report_retry)
    reconcile = RecoverOrphanedSessions(SessionRegistry(), blob_storage, state_repo, report_event, policy)

    remaining_first_pass = await reconcile.execute()
    assert remaining_first_pass == 1
    assert state_repo.store  # still there, not lost

    remaining_second_pass = await reconcile.execute()
    assert remaining_second_pass == 0
    assert state_repo.store == {}


async def test_reconciler_never_touches_a_session_live_in_this_process():
    """The severe bug this test guards against: a periodic reconciler pass
    running concurrently with an active recording must not complete/abort
    its multipart upload out from under it just because the state file (kept
    up to date by AppendAudio) shows status=RECORDING -- RECORDING is the
    normal, expected state for something that's genuinely still live.
    """
    policy = RecordingPolicy(part_size_bytes=1000)
    registry, blob_storage, state_repo, event_reporter, start, append, _, reconcile, _ = _wire(policy)

    session = await start.execute(
        room_id="room-8",
        track_id="track-8",
        participant_identity="heidi",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-8/heidi-mic-audio-vwx234.pcm",
    )
    await asyncio.sleep(0)  # let the fire-and-forget recording.started report run
    await append.execute(session.session_id, b"still-recording-right-now")

    remaining = await reconcile.execute()

    assert remaining == 0
    assert blob_storage.uploads[session.upload_id]["completed"] is False  # untouched
    assert blob_storage.uploads[session.upload_id]["aborted"] is False
    # "recording.started" already fired when the session began -- that's the
    # whole point of D26 (the track is visible while still recording); no
    # terminal event fires since the reconciler correctly left it alone.
    assert event_reporter.events == [(session.session_id, "recording.started")]
    assert registry.get(session.session_id) is not None  # still live
    assert session.status == RecordingStatus.RECORDING


async def test_late_reconnect_after_grace_timeout_claimed_the_session_gets_a_fresh_upload():
    """Covers the race between start_recording.py's resume path and
    stop_recording.py's _grace_timeout claim: if the timeout already claimed
    the session (status=FINALIZING) by the time a late reconnect arrives,
    the reconnect must not resume the dying session -- it should open a
    fresh one instead.
    """
    _, blob_storage, state_repo, _, start, _, _, _, _ = _wire()

    original = await start.execute(
        room_id="room-9",
        track_id="track-9",
        participant_identity="ivan",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-9/ivan-mic-audio-yz5678.pcm",
    )
    # Simulate _grace_timeout having already won the race and claimed the
    # session for finalization (it sets FINALIZING under active.lock before
    # start_recording.py gets a chance to resume it).
    original.status = RecordingStatus.FINALIZING

    late_reconnect = await start.execute(
        room_id="room-9",
        track_id="track-9",
        participant_identity="ivan",
        source="mic",
        sample_rate=16000,
        channels=1,
        bucket="rec",
        object_key="room-9/ivan-mic-audio-yz5678-new.pcm",
    )

    assert late_reconnect.upload_id != original.upload_id  # fresh upload, not the dying one
    assert late_reconnect.status == RecordingStatus.RECORDING


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
