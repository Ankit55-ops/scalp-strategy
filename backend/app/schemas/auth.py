from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: EmailStr
    is_active: bool
    role: str
    timezone: str

    model_config = {"from_attributes": True}


class WorkshopCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
