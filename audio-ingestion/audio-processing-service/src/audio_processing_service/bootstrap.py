"""Wiring: construct adapters + processor + queue service.

Mirrors record-service/src/record_service/bootstrap.py's role/shape.
"""

from __future__ import annotations

from dataclasses import dataclass

from audio_processing_service.config import Config, get_config
from audio_processing_service.infra.event_reporter import EventReporter
from audio_processing_service.infra.storage import S3Storage
from audio_processing_service.services.derivative_processor import DerivativeProcessor
from audio_processing_service.services.redis_derivative_queue_service import (
    RedisDerivativeQueueService,
)


@dataclass
class Application:
    config: Config
    storage: S3Storage
    event_reporter: EventReporter
    processor: DerivativeProcessor
    queue_service: RedisDerivativeQueueService


def build_application(config: Config | None = None) -> Application:
    config = config or get_config()

    storage = S3Storage(config.minio)
    event_reporter = EventReporter(config.orchestrator)
    processor = DerivativeProcessor(config, storage, event_reporter)
    queue_service = RedisDerivativeQueueService()
    queue_service.set_processor(processor.process)

    return Application(
        config=config,
        storage=storage,
        event_reporter=event_reporter,
        processor=processor,
        queue_service=queue_service,
    )
