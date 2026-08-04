"""Entrypoint: connect Redis Stream consumer, run until SIGTERM/SIGINT
(audio-ingestion PLAN.md section 4 / Phase 5). Mirrors
record-service/src/record_service/main.py's shape.
"""

from __future__ import annotations

import asyncio
import logging
import signal

from audio_processing_service.bootstrap import build_application
from audio_processing_service.config import get_config

logger = logging.getLogger(__name__)


async def serve() -> None:
    # Checked before build_application() -- S3Storage.__init__ constructs a
    # boto3 client eagerly (see infra/storage.py), which raises a much less
    # helpful botocore ValueError if MINIO_ENDPOINT is unset.
    config = get_config()
    if not config.minio.is_configured():
        raise RuntimeError("MinIO not configured (check MINIO_* env vars)")

    app = build_application(config)
    await app.queue_service.start()
    logger.info("audio-processing-service running, consuming audio_derivative:stream")

    shutdown = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, shutdown.set)

    await shutdown.wait()
    logger.info("Shutting down...")

    await app.queue_service.stop()
    await app.event_reporter.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())


if __name__ == "__main__":
    main()
