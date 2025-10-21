from pydantic import BaseModel

class CancelRequest(BaseModel):
    room_name: str
