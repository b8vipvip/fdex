from datetime import datetime
from typing import Literal

from pydantic import BaseModel

StorageMode = Literal["local_only", "cloud", "hybrid", "temporary"]
RetentionPolicy = Literal["keep_forever", "delete_after_analysis", "delete_after_1_day", "delete_after_7_days", "delete_after_30_days"]
PrivacyLevel = Literal["public", "normal", "sensitive", "highly_sensitive"]
PrivacyDecision = Literal["desensitize", "temporary", "local_only", "confirm_upload"]


class DetectedPrivacyItem(BaseModel):
    type: str
    count: int
    examples: list[str] = []


class PrivacyDetectionRead(BaseModel):
    is_sensitive: bool
    privacy_level: str
    detected_items: list[DetectedPrivacyItem] = []
    suggested_action: str


class PrivacyDecisionRequest(BaseModel):
    decision: PrivacyDecision


class PrivacySummaryRead(BaseModel):
    project_id: int
    storage_mode: str
    data_retention_policy: str
    allow_third_party_ai: bool
    auto_desensitize: bool
    total_assets: int
    sensitive_assets: int
    highly_sensitive_assets: int
    pending_decision_assets: int
    retention_deadline: datetime | None = None
