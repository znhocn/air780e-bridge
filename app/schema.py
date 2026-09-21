"""Pydantic request/response models"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=8, max_length=128)


class Page(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class ApiKeyCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class NotifyIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field(..., description="dingtalk/wecom/feishu/telegram/email/webhook/apprise")
    enabled: bool = True
    match_from: str = ""
    match_contains: str = ""
    params: dict = Field(default_factory=dict)


class TaskIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True
    interval_days: int = Field(7, ge=1, le=365)
    phone: str = Field(..., min_length=1)
    content: str = Field(..., min_length=1)


class ContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    phone: str = Field(..., max_length=40)
    note: str = Field("", max_length=200)