"""Concurrency benchmark for record-service (PLAN.md D13 action item).

Opens N concurrent StreamAudio sessions against a *running* record-service
instance, feeding each one realistic 16-bit PCM silence at real capture
cadence, while sampling the target process's CPU%/RSS. Sweeps a list of
concurrency levels to find where a single instance/core starts to strain --
see deploy/systemd/README.md and PLAN.md D13/D14 for how the result feeds
into systemd instance-count capacity planning.

Sessions are spread across several gRPC channels (--sessions-per-channel,
default 5), not one giant multiplexed channel -- in production, load comes
from many separate agent worker processes each with their own channel
(agents/src/services/record_service_client.py: one channel per worker,
reused across that worker's tracks), typically a handful of tracks each,
not hundreds on one connection. One shared channel here would benchmark a
traffic pattern that doesn't actually happen.

No second machine needed, but pin the target and this script to separate
cores so the generator's own CPU usage doesn't pollute the measurement --
e.g. from the record-service repo root:

    taskset -c 0 python -m record_service.main &
    taskset -c 1 python scripts/benchmark_concurrency.py --sweep 10,25,50,100

Target PID is auto-detected via `pgrep -f record_service.main` if exactly
one match is found; pass --pid explicitly if you're running multiple
instances (systemd template units) or auto-detect finds >1/0 matches.

Usage: python scripts/benchmark_concurrency.py [options]  (run from repo root)
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import csv
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, "src")

import grpc  # noqa: E402
import psutil  # noqa: E402

from record_service.infra.grpc import recording_pb2, recording_pb2_grpc  # noqa: E402


def _autodetect_pid() -> int:
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", "record_service.main"], text=True
        ).split()
    except subprocess.CalledProcessError:
        out = []
    pids = [int(p) for p in out if int(p) != __import__("os").getpid()]
    if len(pids) == 1:
        return pids[0]
    if not pids:
        raise SystemExit(
            "Could not auto-detect record-service PID (pgrep -f record_service.main "
            "found nothing running) -- start it first, or pass --pid explicitly."
        )
    raise SystemExit(
        f"Found {len(pids)} matching processes ({pids}) -- ambiguous (multiple "
        f"instances?). Pass --pid explicitly."
    )


def _silence_frame(sample_rate: int, channels: int, frame_ms: int) -> bytes:
    n_samples = sample_rate * frame_ms // 1000
    return b"\x00\x00" * n_samples * channels  # PCM16 silence, 2 bytes/sample


class _Session:
    """Mirrors RecordForwarder's queue + drop-on-full pattern
    (agents/src/services/record_service_client.py) so backpressure behaves
    like a real agent under load, not an unbounded blast that would hide
    exactly the kind of saturation this benchmark exists to find."""

    def __init__(
        self,
        stub: recording_pb2_grpc.RecordingIngestStub,
        room_id: str,
        track_id: str,
        sample_rate: int,
        channels: int,
        max_queue_size: int,
    ) -> None:
        self._stub = stub
        self._room_id = room_id
        self._track_id = track_id
        self._sample_rate = sample_rate
        self._channels = channels
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=max_queue_size)
        self._call = None
        self._writer_task: asyncio.Task | None = None
        self._reader_task: asyncio.Task | None = None
        self.frames_sent = 0
        self.frames_dropped = 0
        self.accepted = 0
        self.rejected = 0

    async def start(self) -> None:
        self._call = self._stub.StreamAudio()
        self._writer_task = asyncio.create_task(self._write_loop())
        self._reader_task = asyncio.create_task(self._read_loop())
        self._queue.put_nowait(
            recording_pb2.AudioChunk(
                start=recording_pb2.SessionStart(
                    room_id=self._room_id,
                    track_id=self._track_id,
                    participant_identity="bench-participant",
                    source="mic",
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                )
            )
        )

    def send(self, pcm: bytes) -> None:
        try:
            self._queue.put_nowait(recording_pb2.AudioChunk(pcm=pcm))
            self.frames_sent += 1
        except asyncio.QueueFull:
            self.frames_dropped += 1

    async def _write_loop(self) -> None:
        try:
            while True:
                item = await self._queue.get()
                if item is None:
                    break
                await self._call.write(item)
        except Exception:  # noqa: BLE001 - benchmark client, best-effort
            pass
        finally:
            with contextlib.suppress(Exception):
                await self._call.done_writing()

    async def _read_loop(self) -> None:
        try:
            async for ack in self._call:
                if ack.status == "accepted":
                    self.accepted += 1
                elif ack.status == "rejected":
                    self.rejected += 1
        except Exception:  # noqa: BLE001
            pass

    async def close(self) -> None:
        with contextlib.suppress(asyncio.QueueFull):
            self._queue.put_nowait(None)
        for task in (self._writer_task, self._reader_task):
            if task is not None:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(task, timeout=5.0)


async def _run_session(
    channel: grpc.aio.Channel,
    level: int,
    index: int,
    duration_s: float,
    frame_ms: int,
    sample_rate: int,
    channels: int,
    max_queue_size: int,
) -> _Session:
    stub = recording_pb2_grpc.RecordingIngestStub(channel)
    session = _Session(
        stub,
        room_id=f"bench-{level}-{index}",
        track_id=f"track-{index}",
        sample_rate=sample_rate,
        channels=channels,
        max_queue_size=max_queue_size,
    )
    await session.start()

    frame = _silence_frame(sample_rate, channels, frame_ms)
    interval = frame_ms / 1000.0
    deadline = time.monotonic() + duration_s
    next_tick = time.monotonic()
    while time.monotonic() < deadline:
        session.send(frame)
        next_tick += interval
        sleep_for = next_tick - time.monotonic()
        if sleep_for > 0:
            await asyncio.sleep(sleep_for)

    await session.close()
    return session


@dataclass
class _Sample:
    t: float
    target_cpu: float
    target_rss_mb: float
    self_cpu: float


async def _monitor(
    target_pid: int, stop: asyncio.Event, samples: list[_Sample], interval: float = 1.0
) -> None:
    target = psutil.Process(target_pid)
    self_proc = psutil.Process()
    target.cpu_percent()  # prime psutil's internal delta counter
    self_proc.cpu_percent()
    start = time.monotonic()
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
        try:
            samples.append(
                _Sample(
                    t=time.monotonic() - start,
                    target_cpu=target.cpu_percent(),
                    target_rss_mb=target.memory_info().rss / (1024 * 1024),
                    self_cpu=self_proc.cpu_percent(),
                )
            )
        except psutil.NoSuchProcess:
            break


@dataclass
class LevelResult:
    concurrency: int
    cpu_target_avg: float
    cpu_target_max: float
    cpu_self_avg: float
    rss_target_mb_avg: float
    frames_sent: int
    frames_dropped: int
    accepted_acks: int
    rejected_acks: int


async def run_level(
    address: str,
    pid: int,
    level: int,
    duration_s: float,
    warmup_s: float,
    frame_ms: int,
    sample_rate: int,
    channels: int,
    max_queue_size: int,
    sessions_per_channel: int,
) -> LevelResult:
    samples: list[_Sample] = []
    stop = asyncio.Event()
    monitor_task = asyncio.create_task(_monitor(pid, stop, samples))

    n_channels = max(1, -(-level // sessions_per_channel))  # ceil division
    channels_pool = [grpc.aio.insecure_channel(address) for _ in range(n_channels)]
    try:
        tasks = [
            asyncio.create_task(
                _run_session(
                    channels_pool[i % n_channels], level, i, duration_s,
                    frame_ms, sample_rate, channels, max_queue_size,
                )
            )
            for i in range(level)
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        stop.set()
        await monitor_task
        for ch in channels_pool:
            await ch.close()

    sessions = [r for r in results if isinstance(r, _Session)]
    # Discard the ramp-up window (tasks starting, queues filling) -- only
    # steady-state samples count towards the reported average/max.
    steady = [s for s in samples if s.t >= warmup_s] or samples or [_Sample(0, 0, 0, 0)]

    cpu_vals = [s.target_cpu for s in steady]
    self_cpu_vals = [s.self_cpu for s in steady]
    rss_vals = [s.target_rss_mb for s in steady]

    return LevelResult(
        concurrency=level,
        cpu_target_avg=sum(cpu_vals) / len(cpu_vals),
        cpu_target_max=max(cpu_vals),
        cpu_self_avg=sum(self_cpu_vals) / len(self_cpu_vals),
        rss_target_mb_avg=sum(rss_vals) / len(rss_vals),
        frames_sent=sum(s.frames_sent for s in sessions),
        frames_dropped=sum(s.frames_dropped for s in sessions),
        accepted_acks=sum(s.accepted for s in sessions),
        rejected_acks=sum(s.rejected for s in sessions),
    )


def _print_row(r: LevelResult) -> None:
    total_frames = r.frames_sent + r.frames_dropped
    drop_rate = r.frames_dropped / total_frames if total_frames else 0.0
    print(
        f"{r.concurrency:>6} | target cpu avg={r.cpu_target_avg:6.1f}% max={r.cpu_target_max:6.1f}% "
        f"| generator cpu avg={r.cpu_self_avg:6.1f}% | rss={r.rss_target_mb_avg:8.1f}MB "
        f"| drop_rate={drop_rate:6.2%} | rejected_acks={r.rejected_acks}"
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--address", default="127.0.0.1:50051", help="record-service gRPC address")
    parser.add_argument("--pid", type=int, default=None, help="target process PID (auto-detected if omitted)")
    parser.add_argument("--sweep", default="10,25,50,100", help="comma-separated concurrency levels, tested in order")
    parser.add_argument("--duration", type=float, default=30.0, help="seconds of load per level")
    parser.add_argument("--warmup", type=float, default=5.0, help="seconds excluded from CPU stats while load ramps up")
    parser.add_argument("--cooldown", type=float, default=5.0, help="pause between levels")
    parser.add_argument("--frame-ms", type=int, default=20, help="PCM frame size in ms (matches real capture cadence)")
    parser.add_argument("--sample-rate", type=int, default=16000)
    parser.add_argument("--channels", type=int, default=1)
    parser.add_argument(
        "--max-queue-size", type=int, default=200,
        help="mirrors RECORD_SERVICE_MAX_QUEUE_SIZE default (agents config)",
    )
    parser.add_argument(
        "--sessions-per-channel", type=int, default=5,
        help="sessions sharing one gRPC channel, mirrors one agent worker's typical track count",
    )
    parser.add_argument(
        "--cpu-stop-threshold", type=float, default=90.0,
        help="stop the sweep once target CPU avg (%% of 1 core) crosses this",
    )
    parser.add_argument("--csv", type=Path, default=None, help="also write results to this CSV path")
    args = parser.parse_args()

    pid = args.pid if args.pid is not None else _autodetect_pid()
    levels = [int(x) for x in args.sweep.split(",") if x.strip()]

    print(f"Target: {args.address} (pid={pid}) | frame={args.frame_ms}ms @ {args.sample_rate}Hz/{args.channels}ch")
    print(f"{'N':>6} | {'target cpu':^28} | {'generator':^20} | {'rss':^10} | drops")

    results: list[LevelResult] = []
    for level in levels:
        result = await run_level(
            args.address, pid, level, args.duration, args.warmup, args.frame_ms,
            args.sample_rate, args.channels, args.max_queue_size, args.sessions_per_channel,
        )
        results.append(result)
        _print_row(result)

        if result.cpu_self_avg >= 80.0:
            print(
                "  WARNING: generator's own CPU avg is high -- pin it to a separate "
                "core (taskset) or results from here on may understate the target's real usage."
            )

        if result.cpu_target_avg >= args.cpu_stop_threshold:
            print(
                f"\nStopping sweep: target CPU avg {result.cpu_target_avg:.1f}% "
                f">= threshold {args.cpu_stop_threshold:.1f}% at concurrency={level}"
            )
            break

        await asyncio.sleep(args.cooldown)

    if args.csv:
        with args.csv.open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "concurrency", "cpu_target_avg", "cpu_target_max", "cpu_self_avg",
                "rss_target_mb_avg", "frames_sent", "frames_dropped",
                "accepted_acks", "rejected_acks",
            ])
            for r in results:
                writer.writerow([
                    r.concurrency, r.cpu_target_avg, r.cpu_target_max, r.cpu_self_avg,
                    r.rss_target_mb_avg, r.frames_sent, r.frames_dropped,
                    r.accepted_acks, r.rejected_acks,
                ])
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    asyncio.run(main())
