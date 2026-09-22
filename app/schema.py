"""Pydantic request/response models"""

from pydantic import BaseModel, Field, field_validator


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


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
    match_from: str = Field("", max_length=100)
    match_contains: str = Field("", max_length=100)
    params: dict = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _cap_params(cls, v):
        if not isinstance(v, dict):
            raise ValueError("params must be an object")
        if len(v) > 50:
            raise ValueError("params must have at most 50 keys")
        for k, val in v.items():
            if val is None:
                continue
            if not isinstance(val, (str, int, float, bool)):
                raise ValueError(f"params.{k}: unsupported value type")
            if isinstance(val, str) and len(val) > 2000:
                raise ValueError(f"params.{k}: value too long")
        return v


class TaskIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    enabled: bool = True
    interval_days: int = Field(7, ge=1, le=365)
    phone: str = Field(..., min_length=1, max_length=40)
    content: str = Field(..., min_length=1, max_length=40000)


class ContactIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    phone: str = Field(..., max_length=40)
    note: str = Field("", max_length=200)