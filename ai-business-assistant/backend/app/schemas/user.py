from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

ProfessionalLevel = str


class UserBase(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=100)
    professional_level: ProfessionalLevel = "business"


class UserCreate(UserBase):
    password: str = Field(min_length=6, max_length=128)


class UserRead(UserBase):
    id: int
    avatar: str = ""
    company_name: str = "我的 AI 公司"
    company_industry: str = ""
    auto_company_mode_enabled: bool = False
    auto_company_mode_requires_confirm: bool = True
    default_auto_group_all_employees: bool = True
    default_industry_required: bool = True
    is_verified_company: bool = False
    realname_verified: bool = False
    deleted_retention_days: int = 7
    created_at: datetime
    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
