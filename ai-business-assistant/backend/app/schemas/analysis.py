from datetime import datetime
from pydantic import BaseModel


class AnalysisResultRead(BaseModel):
    id: int
    asset_id: int
    project_id: int
    analyzer_type: str
    summary: str
    structured_json: str
    created_at: datetime
    model_config = {"from_attributes": True}


class AnalyzeProjectResponse(BaseModel):
    project_type: str
    requirement_score: float
    reports: list["ReportRead"] = []

from app.schemas.document import ReportRead  # noqa: E402
AnalyzeProjectResponse.model_rebuild()
