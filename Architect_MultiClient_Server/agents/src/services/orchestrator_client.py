"""
Orchestrator Service Client - Handles communication with orchestrator API
"""
import asyncio
import httpx
import time
from typing import Optional
from src.logger import get_logger
from src.config.application_config import get_config

logger = get_logger(__name__)
config = get_config()


async def register_room(room_name: str, start_time: Optional[float] = None) -> bool:
    """
    Register a room with the orchestrator service for webhook processing.
    
    Args:
        room_name: Name of the room to register
        start_time: Start time (Unix timestamp). If None, current time is used.
    
    Returns:
        True if registration successful, False otherwise
    """
    if not config.orchestrator.base_url:
        logger.warning("Orchestrator base URL not configured, skipping room registration")
        return False
    
    # Check if event loop is running
    try:
        loop = asyncio.get_running_loop()
        if loop.is_closed():
            logger.warning("Event loop is closed, cannot register room")
            return False
    except RuntimeError:
        logger.warning("No running event loop, cannot register room")
        return False
    
    url = f"{config.orchestrator.base_url}/api/room-registry/register"
    
    payload = {
        "room_name": room_name,
        "start_time": start_time or time.time()
    }
    
    headers = {}
    if config.orchestrator.api_key:
        headers["X-API-Key"] = config.orchestrator.api_key
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                result = response.json()
                logger.info(f"✅ Room '{room_name}' registered successfully with orchestrator")
                return True
            elif response.status_code == 409:
                logger.warning(f"Room '{room_name}' already registered")
                return True  # Consider already registered as success
            else:
                text = response.text
                logger.error(f"Failed to register room '{room_name}': HTTP {response.status_code} - {text}")
                return False
    except httpx.HTTPError as e:
        logger.error(f"Network error registering room '{room_name}': {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error registering room '{room_name}': {e}", exc_info=True)
        return False


async def unregister_room(room_name: str) -> bool:
    """
    Unregister a room from the orchestrator service.
    
    Args:
        room_name: Name of the room to unregister
    
    Returns:
        True if unregistration successful, False otherwise
    """
    if not config.orchestrator.base_url:
        logger.warning("Orchestrator base URL not configured, skipping room unregistration")
        return False
    
    # Check if event loop is running
    try:
        loop = asyncio.get_running_loop()
        if loop.is_closed():
            logger.warning("Event loop is closed, cannot unregister room")
            return False
    except RuntimeError:
        logger.warning("No running event loop, cannot unregister room")
        return False
    
    url = f"{config.orchestrator.base_url}/api/room-registry/unregister"
    
    payload = {
        "room_name": room_name
    }
    
    headers = {}
    if config.orchestrator.api_key:
        headers["Authorization"] = f"Bearer {config.orchestrator.api_key}"
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            if response.status_code in [200, 201]:
                result = response.json()
                logger.info(f"✅ Room '{room_name}' unregistered successfully from orchestrator")
                return True
            elif response.status_code == 404:
                logger.warning(f"Room '{room_name}' not found in registry")
                return True  # Consider not found as success
            else:
                text = response.text
                logger.error(f"Failed to unregister room '{room_name}': HTTP {response.status_code} - {text}")
                return False
    except httpx.HTTPError as e:
        logger.error(f"Network error unregistering room '{room_name}': {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error unregistering room '{room_name}': {e}", exc_info=True)
        return False
