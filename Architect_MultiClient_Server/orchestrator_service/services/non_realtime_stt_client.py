import logging
from typing import Any, Dict, Optional, Tuple

import httpx
from fastapi import HTTPException, status

from orchestrator_service.config.application_config import get_config
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class NonRealtimeSTTClient:
    """
    HTTP Client for communicating with the internal Non-Realtime STT Service (Parakeet ASR).
    Maintains persistent HTTP connection pool for low latency and high concurrency.
    """

    def __init__(self):
        self.config = get_config().non_realtime_stt
        self.base_url = self.config.base_url.rstrip("/")
        self.timeout = self.config.timeout
        self._client: Optional[httpx.AsyncClient] = None

    def _get_client(self) -> httpx.AsyncClient:
        """Get or initialize persistent HTTP client with connection pooling."""
        if self._client is None or self._client.is_closed:
            limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout, connect=10.0),
                limits=limits,
            )
        return self._client

    async def close(self) -> None:
        """Close persistent HTTP client session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _send_transcribe_request(
        self,
        endpoint_path: str,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Internal helper to dispatch transcription requests (sync or async)."""
        endpoint = f"{self.base_url}{endpoint_path}"
        client = self._get_client()

        try:
            if file_bytes and filename:
                files = {"file": (filename, file_bytes, "application/octet-stream")}
                response = await client.post(endpoint, files=files)
            elif url:
                response = await client.post(endpoint, data={"url": url})
            else:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Either 'file' or 'url' must be provided.",
                )

            if response.status_code >= 400:
                try:
                    err_detail = response.json().get("detail", response.text)
                except Exception:
                    err_detail = response.text
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"STT Service Error: {err_detail}",
                )

            return response.json()

        except httpx.ConnectError as e:
            logger.error("Failed to connect to STT service at %s: %s", endpoint, e)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Non-Realtime STT Service is currently unreachable.",
            )
        except httpx.TimeoutException as e:
            logger.error("Timeout waiting for STT service response from %s: %s", endpoint, e)
            raise HTTPException(
                status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                detail="Non-Realtime STT Service request timed out.",
            )

    async def transcribe_sync(
        self,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Forward synchronous transcription request.
        Waits for GPU batch inference to complete and returns full transcript.
        """
        return await self._send_transcribe_request(
            endpoint_path="/api/v1/stt/transcribe",
            file_bytes=file_bytes,
            filename=filename,
            url=url,
        )

    async def transcribe_async(
        self,
        file_bytes: Optional[bytes] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Forward asynchronous transcription request.
        Enqueues task and returns job_id immediately.
        """
        return await self._send_transcribe_request(
            endpoint_path="/api/v1/stt/transcribe/async",
            file_bytes=file_bytes,
            filename=filename,
            url=url,
        )

    async def get_job_status(self, job_id: str) -> Dict[str, Any]:
        """Query job lifecycle status (queued, inprogress, done, failed)."""
        endpoint = f"{self.base_url}/api/v1/stt/jobs/{job_id}/status"
        client = self._get_client()

        try:
            response = await client.get(endpoint)
            if response.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"STT Job '{job_id}' not found.",
                )
            if response.status_code >= 400:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=response.text,
                )
            return response.json()

        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Non-Realtime STT Service is unreachable.",
            )

    async def get_job_result(self, job_id: str) -> Tuple[int, Dict[str, Any]]:
        """Query job result. Returns status code (200 or 202) and response payload."""
        endpoint = f"{self.base_url}/api/v1/stt/jobs/{job_id}/result"
        client = self._get_client()

        try:
            response = await client.get(endpoint)
            if response.status_code == 404:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"STT Job '{job_id}' not found.",
                )
            return response.status_code, response.json()

        except httpx.ConnectError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Non-Realtime STT Service is unreachable.",
            )


_client_instance: Optional[NonRealtimeSTTClient] = None


def get_non_realtime_stt_client() -> NonRealtimeSTTClient:
    """Singleton getter for NonRealtimeSTTClient."""
    global _client_instance
    if _client_instance is None:
        _client_instance = NonRealtimeSTTClient()
    return _client_instance
