"""
Notification Producer Service - Sends notification tasks to Redis Stream

Helper service to easily send notifications to Mezon channels.
"""

from typing import Any, Dict, Optional
from orchestrator_service.utils.logger import get_logger
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.config.application_config import get_config
from orchestrator_service.services.redis.redis_producer_service import RedisProducerService
from orchestrator_service.models.notification_task import NotificationTask

logger = get_logger(__name__)


@singleton
class NotificationProducerService:
    """
    Service for producing notification tasks to Redis Stream.

    Simplified to support generic notifications with title and message dict.
    Message dict can contain different formats (text, embeds, mentions, markdown, etc).
    """

    def __init__(self):
        """Initialize notification producer service"""
        self._config = get_config().notification
        self._producer: Optional[RedisProducerService[NotificationTask]] = None

    async def connect(self) -> None:
        """
        Establish connection to Redis producer service.

        Raises:
            ConnectionError: If cannot connect to Redis
        """
        try:
            self._producer = RedisProducerService.get_instance(
                task_class=NotificationTask,
                stream_key=self._config.stream_key,
            )

            await self._producer.connect()
            logger.info("✅ NotificationProducerService connected")
        except Exception as e:
            logger.error(f"❌ Failed to connect producer: {e}", exc_info=True)
            raise

    async def disconnect(self) -> None:
        """Disconnect producer"""
        if self._producer:
            await self._producer.close()
            logger.info("NotificationProducerService disconnected")

    async def send(
        self,
        title: str,
        message: Dict[str, Any],
    ) -> bool:
        """
        Send a notification with title and message dict.

        Args:
            title: Notification title/subject
            message: Message content as dict.

        Returns:
            True if task was enqueued successfully

        Example:
            await producer.send(
                title="Room Registration Error",
                message={
                    "text": "Room 'my-room' is already registered",
                }
            )
        """
        task = NotificationTask(
            title=title,
            message=message,
        )

        return await self.send_notification_task(task)

    async def send_notification_task(self, task: NotificationTask) -> bool:
        """
        Send a notification task to Redis Stream.

        Args:
            task: NotificationTask to send

        Returns:
            True if task was enqueued successfully
        """
        if not self._producer:
            await self.connect()

        try:
            message_id = await self._producer.enqueue(task)

            logger.info(f"📤 Notification task enqueued: {task.title} " f"(message_id: {message_id})")

            return True
        except Exception as e:
            logger.error(f"❌ Failed to enqueue notification task: {e}", exc_info=True)
            return False
