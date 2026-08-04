"""Entrypoint: gRPC server + startup reconciliation + periodic reconciler
(PLAN.md D5 tier 3 / Phase 1).
"""

from __future__ import annotations

import asyncio
import logging
import signal

import grpc

from record_service.bootstrap import Application, build_application
from record_service.infra.grpc import recording_pb2_grpc

logger = logging.getLogger(__name__)


async def _reconciler_loop(app: Application, interval_seconds: float, stop_event: asyncio.Event) -> None:
    while not stop_event.is_set():
        try:
            remaining = await app.recover_orphaned_sessions.execute()
            if remaining:
                logger.info("Reconciler: %d session(s) still pending delivery", remaining)
        except Exception:  # noqa: BLE001 - background loop must survive a single bad pass
            logger.exception("Reconciler pass failed, will retry on next tick")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            pass


async def serve() -> None:
    app = build_application()

    logger.info("Running startup reconciliation (PLAN.md D5 tier 3)...")
    remaining = await app.recover_orphaned_sessions.execute()
    if remaining:
        logger.warning("%d session(s) still unreported after startup reconciliation", remaining)

    server = grpc.aio.server()
    recording_pb2_grpc.add_RecordingIngestServicer_to_server(app.ingest_servicer, server)
    bind_addr = f"{app.config.grpc.host}:{app.config.grpc.port}"
    server.add_insecure_port(bind_addr)

    stop_event = asyncio.Event()
    reconciler_task = asyncio.create_task(
        _reconciler_loop(app, app.config.reconciler.interval_seconds, stop_event)
    )

    await server.start()
    logger.info("record-service listening on %s", bind_addr)

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    await shutdown.wait()
    logger.info("Shutting down...")

    stop_event.set()
    await reconciler_task
    await app.event_reporter.close()
    await server.stop(grace=10)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
