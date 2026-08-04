"""BlobStorage adapter backed by MinIO/S3 (boto3).

boto3 is sync -- calls are pushed to a thread via asyncio.to_thread, same
pattern as the existing agents/src/services/audio_recording_manager.py this
was lifted from (PLAN.md D1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import boto3
from botocore.config import Config as BotoConfig

from record_service.config import MinIOConfig
from record_service.domain.models import UploadedPart
from record_service.domain.ports import BlobStorage


class S3BlobStorage(BlobStorage):
    def __init__(self, config: MinIOConfig) -> None:
        self._config = config
        # Eager, not lazy: every session uses this client (there's no
        # "constructed but never called" case), and boto3.client(...) does no
        # I/O at construction time -- it only opens connections lazily on
        # first request. Building it here avoids a check-then-create race in
        # concurrent callers (StartRecording.execute is now locked per
        # session_id, not globally, so multiple sessions can reach this
        # adapter for the first time at once).
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

    async def create_multipart_upload(self, bucket: str, key: str) -> str:
        response = await asyncio.to_thread(
            self._client.create_multipart_upload,
            Bucket=bucket,
            Key=key,
            ContentType="application/octet-stream",
        )
        return response["UploadId"]

    async def upload_part(
        self, bucket: str, key: str, upload_id: str, part_number: int, data: bytes
    ) -> str:
        response = await asyncio.to_thread(
            self._client.upload_part,
            Bucket=bucket,
            Key=key,
            PartNumber=part_number,
            UploadId=upload_id,
            Body=data,
        )
        return response["ETag"]

    async def complete_multipart_upload(
        self, bucket: str, key: str, upload_id: str, parts: Sequence[UploadedPart]
    ) -> None:
        await asyncio.to_thread(
            self._client.complete_multipart_upload,
            Bucket=bucket,
            Key=key,
            UploadId=upload_id,
            MultipartUpload={
                "Parts": [{"PartNumber": p.part_number, "ETag": p.etag} for p in parts]
            },
        )

    async def abort_multipart_upload(self, bucket: str, key: str, upload_id: str) -> None:
        await asyncio.to_thread(
            self._client.abort_multipart_upload, Bucket=bucket, Key=key, UploadId=upload_id
        )
