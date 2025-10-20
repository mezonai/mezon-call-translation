from pydantic import BaseModel

class JobRequest(BaseModel):
    url: str
