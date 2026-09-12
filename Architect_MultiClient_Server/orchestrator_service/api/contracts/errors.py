from pydantic import BaseModel, Field


class ErrorDetail(BaseModel): # type: ignore[explicit-any]
    location: list[str | int] | None = None
    message: str
    type: str | None = None

class ErrorResponse(BaseModel): # type: ignore[explicit-any]
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
