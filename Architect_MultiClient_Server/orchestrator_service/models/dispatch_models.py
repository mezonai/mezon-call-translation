from typing import Annotated
from uuid import UUID

from fastapi import Path
from pydantic import BaseModel, Field


class DispatchRequestModel(BaseModel): # type: ignore[explicit-any]
    room_name: str = Field(
        ...,
        min_length=1,
        description="Room name"
    )

RoomIdPath = Annotated[
    UUID,
    Path(description="Room ID")
]
