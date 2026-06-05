from datetime import datetime
from pydantic import BaseModel, Field


class ProjectBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""


class ProjectCreate(ProjectBase):
    professional_level: str | None = None


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None


class ProjectRead(ProjectBase):
    id: int
    user_id: int
    project_type: str
    status: str
    requirement_score: float
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str = Field(min_length=1)
    role: str = "user"


class MessageRead(MessageCreate):
    id: int
    project_id: int
    created_at: datetime
    model_config = {"from_attributes": True}
