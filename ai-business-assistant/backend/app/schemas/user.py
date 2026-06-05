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
    created_at: datetime
    model_config = {"from_attributes": True}


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
