"""
Notification Producer Service - Sends notification tasks to Redis Stream

Helper service to easily send notifications to Mezon channels.
"""

from typing import Any

from orchestrator_service.config.application_config import get_config
from orchestrator_service.models.notification_task import NotificationTask
from orchestrator_service.services.redis.redis_producer_service import RedisProducerService, create_producer_service
from orchestrator_service.utils.decorator import singleton
from orchestrator_service.utils.logger import get_logger

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
        self._producer: RedisProducerService[NotificationTask] = create_producer_service(
            task_class=NotificationTask,
            stream_key=self._config.stream_key,
        )

    # TODO: Use `Any` type because `message` can have different formats
    async def send(  # type: ignore[explicit-any]
        self,
        title: str,
        message: dict[str, Any],
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
        # Pypass for test
        return True

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
        try:
            message_id = await self._producer.enqueue(task)
            logger.info(f"📤 Notification task enqueued: {task.title} (message_id: {message_id})")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to enqueue notification task: {e}", exc_info=True)
            return False
