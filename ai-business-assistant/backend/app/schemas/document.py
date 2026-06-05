from datetime import datetime
from pydantic import BaseModel


class ReportRead(BaseModel):
    id: int
    project_id: int
    report_type: str
    title: str
    content_markdown: str
    structured_json: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}
