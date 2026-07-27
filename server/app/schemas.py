from datetime import datetime

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    time: datetime


class VersionResponse(BaseModel):
    service: str
    version: str
    environment: str
