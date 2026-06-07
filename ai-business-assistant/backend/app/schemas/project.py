from datetime import datetime
from pydantic import BaseModel, Field
from app.schemas.privacy import RetentionPolicy, StorageMode


class ProjectBase(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    storage_mode: StorageMode = "hybrid"
    data_retention_policy: RetentionPolicy = "keep_forever"
    allow_third_party_ai: bool = True
    auto_desensitize: bool = True


class ProjectCreate(ProjectBase):
    professional_level: str | None = None
    start_auto_operation: bool = False


class ProjectUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    storage_mode: StorageMode | None = None
    data_retention_policy: RetentionPolicy | None = None
    allow_third_party_ai: bool | None = None
    auto_desensitize: bool | None = None


class ProjectRead(ProjectBase):
    id: int
    user_id: int
    project_type: str
    status: str
    requirement_score: float
    industry: str = ""
    auto_operation_status: str = "not_started"
    auto_operation_group_id: int | None = None
    stage: str = ""
    stage_summary: str = ""
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
