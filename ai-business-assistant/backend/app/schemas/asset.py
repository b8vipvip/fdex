from datetime import datetime
from pydantic import BaseModel


class AssetRead(BaseModel):
    id: int
    project_id: int
    filename: str
    original_filename: str
    file_type: str
    mime_type: str
    file_size: int
    status: str
    created_at: datetime
    model_config = {"from_attributes": True}
