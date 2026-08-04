"""MinIO/S3 storage adapter -- download the raw capture to a local temp
file, upload the transcoded derivative back.

boto3 is sync -- calls pushed to a thread via asyncio.to_thread, same
pattern as record-service/src/record_service/infra/storage/s3_blob_storage.py
this was lifted from. File-based (not streamed through ffmpeg's pipes) on
purpose -- audio-processing-service isn't on any critical path (PLAN.md D7),
so the simplicity of "download whole file, transcode whole file, upload
whole file" outweighs the memory/latency cost of buffering a call's raw PCM
locally (worst case a few hundred MB for a very long call).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from audio_processing_service.config import MinIOConfig


class S3Storage:
    def __init__(self, config: MinIOConfig) -> None:
        self._config = config
        # Eager, not lazy -- see record-service's S3BlobStorage for the same
        # reasoning (no I/O at construction time, avoids a check-then-create
        # race between concurrently-processed tasks).
        addressing_style = "path" if self._config.force_path_style else "virtual"
        self._client = boto3.client(
            "s3",
            endpoint_url=self._config.endpoint,
            aws_access_key_id=self._config.access_key,
            aws_secret_access_key=self._config.secret_key,
            region_name=self._config.region,
            use_ssl=self._config.secure,
            config=BotoConfig(s3={"addressing_style": addressing_style}),
        )

    async def download_to_file(self, bucket: str, key: str, local_path: Path) -> None:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(
            self._client.download_file, Bucket=bucket, Key=key, Filename=str(local_path)
        )

    async def upload_file(self, bucket: str, key: str, local_path: Path) -> None:
        await asyncio.to_thread(
            self._client.upload_file, Filename=str(local_path), Bucket=bucket, Key=key
        )
