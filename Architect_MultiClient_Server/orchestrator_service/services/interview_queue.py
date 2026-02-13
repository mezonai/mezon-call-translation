"""
Interview Queue Service - Singleton for managing interview room mappings
"""
from typing import Dict, Optional
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class InterviewQueue:
    """
    Singleton service to manage interview room mappings.
    
    Lifecycle:
    1. When interview dispatch is created: {room_name: interview_id}
    2. When room is registered: update to {room_id: interview_id}
    3. When summary is generated: retrieve interview_id by room_id and send to webhook
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        # Store mappings: key can be room_name or room_id, value is interview_id
        self._queue: Dict[str, str] = {}
        self._initialized = True
        logger.info("InterviewQueue initialized")
    
    def add_by_room_name(self, room_name: str, interview_id: str) -> bool:
        """
        Add interview mapping by room_name.
        Called when dispatch is created with interview type.
        
        Args:
            room_name: LiveKit room name
            interview_id: Interview identifier
            
        Returns:
            True if added successfully
        """
        if not room_name or not interview_id:
            logger.warning(f"Invalid parameters: room_name={room_name}, interview_id={interview_id}")
            return False
        
        self._queue[room_name] = interview_id
        logger.info(f"✅ Interview added: room_name='{room_name}' → interview_id='{interview_id}'")
        return True
    
    def update_to_room_id(self, room_name: str, room_id: str) -> bool:
        """
        Update mapping from room_name to room_id when room is registered.
        
        Args:
            room_name: LiveKit room name
            room_id: MongoDB room _id
            
        Returns:
            True if updated successfully, False if room_name not found
        """
        if room_name not in self._queue:
            logger.debug(f"Room '{room_name}' not in interview queue, skipping update")
            return False
        
        interview_id = self._queue[room_name]
        
        # Remove old room_name mapping
        del self._queue[room_name]
        
        # Add new room_id mapping
        self._queue[room_id] = interview_id
        
        logger.info(f"✅ Interview mapping updated: room_name='{room_name}' → room_id='{room_id}', interview_id='{interview_id}'")
        return True
    
    def get_interview_id(self, key: str) -> Optional[str]:
        """
        Get interview_id by room_name or room_id.
        
        Args:
            key: Either room_name or room_id
            
        Returns:
            interview_id if found, None otherwise
        """
        return self._queue.get(key)
    
    def remove(self, key: str) -> bool:
        """
        Remove interview mapping.
        
        Args:
            key: Either room_name or room_id
            
        Returns:
            True if removed, False if not found
        """
        if key in self._queue:
            interview_id = self._queue.pop(key)
            logger.info(f"🗑️ Interview mapping removed: key='{key}', interview_id='{interview_id}'")
            return True
        return False
    
    def clear(self):
        """Clear all interview mappings."""
        count = len(self._queue)
        self._queue.clear()
        logger.info(f"🗑️ Interview queue cleared: {count} mappings removed")
    
    def list_all(self) -> Dict[str, str]:
        """Get all interview mappings."""
        return self._queue.copy()
    
    def count(self) -> int:
        """Get count of interview mappings."""
        return len(self._queue)


def get_interview_queue() -> InterviewQueue:
    """Get the singleton interview queue instance."""
    return InterviewQueue()
