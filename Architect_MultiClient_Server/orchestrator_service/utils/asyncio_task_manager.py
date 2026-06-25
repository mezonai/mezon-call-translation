"""
Asyncio task manager to manage task lifecycle and prevent memory leaks
"""
import asyncio
from collections.abc import Coroutine
from typing import Any

# Set containing strong references to tasks - prevents them from being garbage collected
# Add task when create it - remove when task is done
_active_tasks: set[asyncio.Task[Any]] = set()

def asyncio_create_task_safety(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """
    Creates an asyncio.Task safely with a self-managed lifecycle.
    Performs 3 steps:
    1. asyncio.create_task(coro) -> assigns to current_task
    2. adds current_task to _active_tasks (keeps a strong reference)
    3. registers add_done_callback -> discards from _active_tasks when done
    """
    # Create task and add to set
    current_task = asyncio.create_task(coro)
    _active_tasks.add(current_task)

    # Request Python to automatically remove task when done
    current_task.add_done_callback(_active_tasks.discard)
    return current_task
