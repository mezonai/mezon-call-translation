"""
Notification Worker Service - Consumes notification tasks from Redis Stream

This worker:
1. Listens to the notifications Redis stream
2. Consumes notification tasks
3. Sends webhooks to Mezon channels
4. Handles retries on failure
"""

import asyncio
import contextlib

import httpx

from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.notification_task import NotificationTask
from orchestrator_service.services.redis.redis_stream_service import RedisStreamService, create_stream_service
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


@singleton
class NotificationWorker:
    """
    Worker for consuming and processing notification tasks from Redis Stream.

    Architecture:
    - Consumes from Redis Stream using Consumer Groups
    - Sends HTTP POST requests to Mezon webhook endpoints
    - Automatically acknowledges successfully processed tasks
    - Retries failed tasks based on configuration

    Usage:
        worker = NotificationWorker()
        await worker.start()    # Start processing tasks
        # ... do other things ...
        await worker.stop()     # Graceful shutdown
    """

    def __init__(self, worker_id: str | None = None):
        """
        Initialize notification worker.

        Args:
            worker_id: Optional custom worker ID (default: auto-generated)
        """
        self._config = get_config().notification
        self._worker_id = worker_id
        self._running = False
        self._consumer_task: asyncio.Task | None = None

        self._stream_service: RedisStreamService[NotificationTask] = create_stream_service(
            task_class=NotificationTask,
            stream_key=self._config.stream_key,
            group_name=self._config.group_name,
        )

        if self._worker_id:
            self._stream_service._consumer_id = self._worker_id

        self._http_client = httpx.AsyncClient(timeout=30.0, follow_redirects=True)

        logger.info(
            f"NotificationWorker initialized - stream='{self._config.stream_key}', group='{self._config.group_name}'"
        )

    async def connect(self) -> None:
        """
        Establish connection and initialize Redis stream service.

        Raises:
            ConnectionError: If cannot connect to Redis
        """
        try:
            await self._stream_service.connect()
            logger.info("✅ NotificationWorker connected to Redis stream")
        except Exception as e:
            logger.error(f"❌ Failed to connect notification worker: {e}", exc_info=True)
            raise

    async def disconnect(self) -> None:
        """Disconnect worker and cleanup resources"""
        await self._stream_service.disconnect()
        await self._http_client.aclose()
        logger.info("NotificationWorker disconnected")

    async def process_task(self, task: NotificationTask) -> bool:
        """
        Process a notification task by sending webhook to Mezon.

        Channel ID and webhook token are taken from configuration.

        Args:
            task: NotificationTask to process

        Returns:
            True if processed successfully, False otherwise
        """
        try:
            logger.info(f"📨 Processing notification: {task.title}")

            # Get channel and webhook credentials from config
            channel_id = self._config.channel_id
            webhook_token = self._config.webhook_token

            if not channel_id or not webhook_token:
                logger.error("❌ Missing notification config: channel_id or webhook_token not configured")
                return False

            # Build webhook URL (default endpoint from config)
            webhook_url = f"{self._config.webhook_endpoint}/{channel_id}/{webhook_token}"

            # Build message payload
            payload = {"type": "hook", "message": task.message}

            # Send webhook
            response = await self._http_client.post(
                webhook_url, json=payload, headers={"Content-Type": "application/json"}
            )

            # Check response
            if response.status_code in [200, 201, 202, 204]:
                logger.info(f"✅ Webhook sent successfully: {response.status_code}")
                return True
            else:
                logger.warning(f"⚠️ Webhook returned status {response.status_code}: {response.text}")
                return False

        except Exception as e:
            logger.error(f"❌ Error sending webhook: {e}", exc_info=True)
            return False

    async def start(self) -> None:
        """
        Start the notification worker.

        This will:
        1. Connect to Redis
        2. Start background consumer task
        """
        if self._running:
            logger.warning("NotificationWorker already running")
            return

        # Ensure connected
        await self.connect()

        self._running = True

        # Start consumer task
        self._consumer_task = asyncio.create_task(self._consumer_loop(), name="notification-worker-consumer")

        logger.info(
            f"✅ NotificationWorker started\n"
            f"   Consumer ID: {self._stream_service._consumer_id}\n"
            f"   Stream: {self._config.stream_key}\n"
            f"   Group: {self._config.group_name}"
        )

    async def stop(self) -> None:
        """
        Stop the notification worker gracefully.

        This will:
        1. Signal worker to stop
        2. Cancel consumer task
        3. Disconnect from Redis
        """
        logger.info("Stopping NotificationWorker...")
        self._running = False

        # Cancel consumer task
        if self._consumer_task:
            self._consumer_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer_task

        # Cleanup
        await self.disconnect()

        logger.info("✅ NotificationWorker stopped")

    async def _consumer_loop(self) -> None:
        """
        Main consumer loop (runs as background task).

        Continuously reads and processes notification tasks from Redis Stream.
        """
        logger.info("🚀 NotificationWorker consumer loop started")
        try:
            while self._running:
                try:
                    # Read next batch of tasks
                    tasks = await self._stream_service.read_tasks(count=10, block_ms=5000)

                    if not tasks:
                        # No tasks available, continue waiting
                        continue

                    # Process each task
                    for task in tasks:
                        success = await self.process_task(task)

                        if success:
                            # Acknowledge successful task
                            await self._stream_service.acknowledge(task)
                            logger.info(f"✅ Task acknowledged: {task.task_id}")
                        else:
                            # Reject task - will retry or move to DLQ automatically
                            error_msg = f"Webhook delivery failed for notification: {task.title}"
                            await self._stream_service.reject(task, error=error_msg, retry=True)
                            # Brief pause before processing next task
                            await asyncio.sleep(self._config.retry_delay_sec)

                except Exception as e:
                    logger.error(f"Error in notification worker loop: {e}", exc_info=True)
                    await asyncio.sleep(1)  # Brief pause before retry

        except asyncio.CancelledError:
            logger.info("NotificationWorker consumer loop cancelled")
        except Exception as e:
            logger.error(f"❌ Unexpected error in consumer loop: {e}", exc_info=True)
        finally:
            logger.info("🛑 NotificationWorker consumer loop stopped")
