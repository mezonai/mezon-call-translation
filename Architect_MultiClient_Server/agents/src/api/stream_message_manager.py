import asyncio

class StreamMessageManager:
    _instance = None
    _lock = asyncio.Lock()

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.queues = {}
        return cls._instance

    async def get_queue(self, room_id):
        async with self._lock:
            if room_id not in self.queues:
                self.queues[room_id] = asyncio.Queue()
            return self.queues[room_id]

stream_message_manager = StreamMessageManager()
