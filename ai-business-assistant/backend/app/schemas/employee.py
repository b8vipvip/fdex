from datetime import datetime
from pydantic import BaseModel, Field


class EmployeeBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    avatar: str = "🤖"
    department: str = "综合管理部"
    position: str = Field(min_length=1, max_length=100)
    role_prompt: str = ""
    industry: str = "通用"
    reply_mode: str = "text"
    can_create_project: bool = False
    can_delete_project: bool = False
    can_view_project_data: bool = True
    can_view_project_reports: bool = True
    can_view_other_employee_messages: bool = False
    can_view_project_progress: bool = True


class EmployeeCreate(EmployeeBase):
    pass


class EmployeeUpdate(BaseModel):
    name: str | None = None
    avatar: str | None = None
    department: str | None = None
    position: str | None = None
    role_prompt: str | None = None
    industry: str | None = None
    reply_mode: str | None = None
    can_create_project: bool | None = None
    can_delete_project: bool | None = None
    can_view_project_data: bool | None = None
    can_view_project_reports: bool | None = None
    can_view_other_employee_messages: bool | None = None
    can_view_project_progress: bool | None = None


class EmployeeRead(EmployeeBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class EmployeeMessageCreate(BaseModel):
    content: str = Field(min_length=1)
    project_id: int | None = None
    message_type: str = "text"


class EmployeeMessageRead(BaseModel):
    id: int
    user_id: int
    employee_id: int
    project_id: int | None
    role: str
    content: str
    message_type: str
    created_at: datetime
    metadata_json: str
    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    user_message: EmployeeMessageRead
    employee_message: EmployeeMessageRead
