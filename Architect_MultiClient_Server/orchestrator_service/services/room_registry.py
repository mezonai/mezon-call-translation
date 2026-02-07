"""
Room Registry Service - Singleton để quản lý active rooms
"""
import time
from typing import Optional, Dict
from orchestrator_service.utils.logger import get_logger

logger = get_logger(__name__)


class RoomRegistry:
    """
    Singleton service để lưu trữ và quản lý active rooms.
    Chỉ các rooms được register mới được xử lý webhook events.
    """
    
    _instance: Optional["RoomRegistry"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
        
        self._rooms: Dict[str, str] = {}  # {room_name: str}
        self._initialized = True
        logger.info("RoomRegistry initialized")
    
    def register_room(self, room_name: str, room_id: str) -> bool:
        """
        Register một room vào registry.
        
        Args:
            room_name: Tên room cần register
            room_id: ID của room 
            
        Returns:
            True nếu register thành công, False nếu room đã tồn tại
        """
        if room_name in self._rooms:
            logger.warning(f"Room '{room_name}' already registered")
            return False
        
        self._rooms[room_name] = room_id
        logger.info(f"Room '{room_name}' registered at {room_id}")
        return True
    
    def unregister_room(self, room_name: str) -> bool:
        """
        Unregister một room khỏi registry.
        
        Args:
            room_name: Tên room cần unregister
            
        Returns:
            True nếu unregister thành công, False nếu room không tồn tại
        """
        if room_name not in self._rooms:
            logger.warning(f"Room '{room_name}' not found in registry")
            return False
        
        start_time = self._rooms.pop(room_name)
        logger.info(f"Room '{room_name}' unregistered (started at: {start_time})")
        return True
    
    def is_registered(self, room_name: str) -> bool:
        """
        Kiểm tra xem room có được register hay không.
        
        Args:
            room_name: Tên room cần kiểm tra
            
        Returns:
            True nếu room đã được register, False nếu chưa
        """
        return room_name in self._rooms
    
    def get_room_start_time(self, room_name: str) -> Optional[str]:
        """
        Lấy thời gian bắt đầu của room.
        
        Args:
            room_name: Tên room
            
        Returns:
            start time type IOS string
        """
        return self._rooms.get(room_name)
    
    def get_room_duration(self, room_name: str) -> Optional[float]:
        """
        Tính thời gian tồn tại của room (giây).
        
        Args:
            room_name: Tên room
            
        Returns:
            Số giây từ khi room được register, hoặc None nếu room không tồn tại
        """
        start_time = self._rooms.get(room_name)
        if start_time is None:
            return None
        return time.time() - start_time
    
    def list_rooms(self) -> Dict[str, float]:
        """
        Lấy danh sách tất cả rooms đang active.
        
        Returns:
            Dictionary {room_name: time_start}
        """
        return self._rooms.copy()
    
    def count_rooms(self) -> int:
        """
        Đếm số lượng rooms đang active.
        
        Returns:
            Số lượng rooms
        """
        return len(self._rooms)
    
    def clear_all(self):
        """Clear tất cả rooms (dùng cho testing hoặc cleanup)"""
        count = len(self._rooms)
        self._rooms.clear()
        logger.info(f"Cleared all {count} rooms from registry")


# Global singleton instance
_room_registry: Optional[RoomRegistry] = None


def get_room_registry() -> RoomRegistry:
    """
    Get the global room registry instance.
    
    Returns:
        RoomRegistry singleton instance
    """
    global _room_registry
    if _room_registry is None:
        _room_registry = RoomRegistry()
    return _room_registry
