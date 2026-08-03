"""
Derivative processor: orchestrates 1 "transcode raw capture -> client
derivative" job (audio-ingestion PLAN.md section 4). Blueprint mirrors
stt_service/service/whisper_transcription_processor.py's process(task) shape.

Failure reporting is retry-count-aware on purpose: a transient failure (e.g.
MinIO blip, ffmpeg OOM-killed once) should NOT flip Track.derivative_status
to "failed" in orchestrator while RedisDerivativeQueueService is still going
to retry the same task -- that would let check_and_notify_room_recordings_ready
(audio-ingestion PLAN.md D19) treat the track as done-but-failed and
potentially fire room_record_done before the retry has a chance to succeed.
So `derivative.failed` is only reported on the attempt that RedisStreamService
is about to send to the dead-letter queue (task.retry_count already at
config.redis.max_retries) -- see reject()'s should_retry formula in
infra/redis/redis_stream_service.py. Earlier attempts fail silently (no
event sent, track stays "pending" in DB) and simply retry.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from audio_processing_service.config import Config
from audio_processing_service.infra.event_reporter import EventReporter
from audio_processing_service.infra.naming import build_derivative_key
from audio_processing_service.infra.storage import S3Storage
from audio_processing_service.infra.transcoder import TranscodeError, transcode_pcm_to_ogg
from audio_processing_service.models.derivative_task import AudioDerivativeStreamTask

logger = logging.getLogger(__name__)


class DerivativeProcessor:
    def __init__(self, config: Config, storage: S3Storage, event_reporter: EventReporter) -> None:
        self._config = config
        self._storage = storage
        self._event_reporter = event_reporter

    async def process(self, task: AudioDerivativeStreamTask) -> None:
        is_final_attempt = task.retry_count >= self._config.redis.max_retries

        with tempfile.TemporaryDirectory(prefix="audio-derivative-") as tmp:
            tmp_dir = Path(tmp)
            raw_path = tmp_dir / "raw.pcm"
            derivative_path = tmp_dir / "derivative.ogg"

            try:
                logger.info(f"⬇️  Downloading {task.bucket}/{task.object_key}")
                await self._storage.download_to_file(task.bucket, task.object_key, raw_path)

                logger.info(f"🎛️  Transcoding {task.object_key} -> OGG/Opus")
                await transcode_pcm_to_ogg(raw_path, derivative_path, self._config.transcode)

                derivative_key = build_derivative_key(task.object_key)
                logger.info(f"⬆️  Uploading {task.bucket}/{derivative_key}")
                await self._storage.upload_file(task.bucket, derivative_key, derivative_path)

                await self._event_reporter.report_completed(
                    recording_id=task.track_id, bucket=task.bucket, object_key=derivative_key
                )
                logger.info(f"✅ Derivative ready for track {task.track_id}: {derivative_key}")

            except (TranscodeError, Exception) as e:  # noqa: BLE001 - must classify+report, not just log
                logger.error(f"❌ Derivative job failed for track {task.track_id}: {e}")

                if is_final_attempt:
                    try:
                        await self._event_reporter.report_failed(
                            recording_id=task.track_id, error=str(e)
                        )
                    except Exception as report_error:  # noqa: BLE001 - best-effort, must not mask original error
                        logger.error(
                            f"Failed to report derivative.failed for track {task.track_id}: {report_error}"
                        )

                raise
