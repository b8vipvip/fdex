from datetime import datetime
from pydantic import BaseModel, Field
from app.schemas.privacy import PrivacyDetectionRead


class LocalOnlyAssetCreate(BaseModel):
    original_filename: str = Field(min_length=1, max_length=255)
    mime_type: str = "application/octet-stream"
    file_size: int = 0


class AssetRead(BaseModel):
    id: int
    project_id: int
    filename: str
    original_filename: str
    file_type: str
    mime_type: str
    file_size: int
    status: str
    privacy_level: str
    is_sensitive: bool
    desensitized_path: str
    original_deleted_at: datetime | None = None
    retention_deadline: datetime | None = None
    privacy_detection: PrivacyDetectionRead | None = None
    created_at: datetime
    model_config = {"from_attributes": True}
