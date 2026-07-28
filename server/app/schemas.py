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


class PublicConfigResponse(BaseModel):
    service: str
    version: str
    public_base_url: str
    api_prefix: str
    ai_provider: str
    ai_model: str
    ai_enabled: bool
